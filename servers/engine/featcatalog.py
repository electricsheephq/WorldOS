"""Feat catalog: the SRD 5.2 feat list (name / full effect text / prerequisite).

Pure module (no MCP, no campaign I/O). Mirrors ``feature_catalog`` and ``itemcatalog`` —
it reads the vendored SRD 5.2.1 dump (``data/srd/srd524/Feat.json`` + ``FeatBenefit.json``,
the Open5e srd-2024 fixtures, CC-BY-4.0) and exposes each feat's full effect text by name so
the level-up planner can present a BROWSABLE list of real feats instead of a blind free-text box
(the level-up planner's one real gap: 17 SRD feats exist but no enumeration tool surfaced them).

This module surfaces the bundled data READ-ONLY — it never authors content, and a feat the dump
doesn't carry simply isn't listed (the caller never fabricates one). Each ``Feat`` record is a
Django fixture ``{model, pk, fields}`` whose ``fields`` carry ``name``, ``desc``, ``prerequisite``
and ``type``; each ``FeatBenefit`` record links to its feat via ``parent`` (the feat's pk) and
carries the specific benefit's ``name`` + ``desc`` — we fold those benefit lines into the feat's
effect text so a planner sees what the feat actually DOES, not just the one-line intro.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2] / "data" / "srd"
_PRIMARY = _ROOT / "srd524"  # canonical SRD 5.2


def _dirs() -> list[Path]:
    """Feat-data dirs in PRECEDENCE order: srd524 first (canonical), then any later pack under
    data/srd/ that carries a Feat.json (first-wins, exactly like feature_catalog._dirs)."""
    dirs = [_PRIMARY]
    if _ROOT.is_dir():
        for sub in sorted(_ROOT.iterdir()):
            if sub.is_dir() and sub != _PRIMARY:
                dirs.append(sub)
    return [d for d in dirs if (d / "Feat.json").exists()]


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def _benefits_by_parent(d: Path) -> dict[str, list[str]]:
    """{feat_pk: [benefit_desc, …]} for the FeatBenefit rows in dir ``d``, in fixture order, so a
    feat's effect text can append its specific benefits (the real rules, not just the intro line)."""
    out: dict[str, list[str]] = {}
    for r in _rows(d / "FeatBenefit.json"):
        if not isinstance(r, dict):
            continue
        f = r.get("fields") or {}
        parent = str(f.get("parent") or "").strip()
        name = str(f.get("name") or "").strip()
        desc = str(f.get("desc") or "").strip()
        if not parent or not desc:
            continue
        line = f"{name}: {desc}" if name else desc
        out.setdefault(parent, []).append(line)
    return out


@functools.lru_cache(maxsize=None)
def _index() -> list[dict]:
    """All SRD feats as ``{name, desc, prerequisite, type}`` in fixture order, deduped by name
    (first-wins across dirs — srd524 canonical). ``desc`` is the feat's intro text plus each of its
    FeatBenefit lines folded in, so the planner shows what the feat actually does. Built once."""
    out: list[dict] = []
    seen: set[str] = set()
    for d in _dirs():
        benefits = _benefits_by_parent(d)
        for r in _rows(d / "Feat.json"):
            if not isinstance(r, dict):
                continue
            f = r.get("fields") or {}
            name = str(f.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue  # first-wins
            seen.add(key)
            intro = str(f.get("desc") or "").strip()
            pk = str(r.get("pk") or "")
            lines = benefits.get(pk, [])
            # Compose the full effect text: the intro, then each named benefit on its own line.
            parts = [p for p in ([intro] + lines) if p]
            desc = "\n".join(parts)
            out.append({
                "name": name,
                "desc": desc,
                "prerequisite": str(f.get("prerequisite") or "").strip(),
                "type": str(f.get("type") or "").strip(),
            })
    return out


def count() -> int:
    """Total distinct feats in the catalog."""
    return len(_index())


def all_feats() -> list[dict]:
    """Every SRD feat, each a COPY of ``{name, desc, prerequisite, type}`` (so a caller can't
    mutate the cache). The browsable feat list for the level-up planner."""
    return [dict(r) for r in _index()]


def find(query: str = "", limit: int = 0) -> list[dict]:
    """Feats whose name OR prerequisite OR effect text matches ``query`` (case-insensitive
    substring), in fixture order. Empty query returns all feats. ``limit`` (>0) caps the result.
    Returns COPIES — never fabricates a feat the dump doesn't carry."""
    q = (query or "").strip().lower()
    rows = _index()
    if q:
        rows = [
            r for r in rows
            if q in r["name"].lower()
            or q in r["prerequisite"].lower()
            or q in r["desc"].lower()
        ]
    out = [dict(r) for r in rows]
    if isinstance(limit, int) and limit > 0:
        out = out[:limit]
    return out


def lookup(name: str) -> Optional[dict]:
    """The full record for a feat by NAME (case-insensitive), or None on a miss — never a
    fabricated entry. Returns a COPY ``{name, desc, prerequisite, type}``."""
    if not name:
        return None
    key = name.strip().lower()
    for r in _index():
        if r["name"].lower() == key:
            return dict(r)
    return None
