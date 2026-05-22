"""ClawDnD rules MCP server.

Read-only D&D 5e rules reference. Serves a bundled SRD 5.2 dataset from
data/srd/ first (offline, canonical, CC-BY-4.0) and falls back to the public
dnd5eapi.co API for anything not bundled. Lookups use fuzzy matching so
"fire ball" finds "Fireball" and "prnoe" finds "Prone".

The bundled dataset ships all 14 conditions plus a starter set of spells,
monsters, and core rules; the live fallback covers the long tail. Set
CLAWDND_RULES_OFFLINE=1 to disable network lookups (used in CI).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from rapidfuzz import fuzz, process

mcp = FastMCP("clawdnd-rules")

_DATA_DIR = Path(
    os.environ.get("CLAWDND_SRD_DIR", Path(__file__).resolve().parents[2] / "data" / "srd")
)
_API_HOST = "https://www.dnd5eapi.co"


def _load(name: str) -> dict[str, dict]:
    p = _DATA_DIR / f"{name}.json"
    if not p.exists():
        return {}
    rows = json.loads(p.read_text(encoding="utf-8"))
    return {row["name"].lower(): row for row in rows}


_CONDITIONS = _load("conditions")
_SPELLS = _load("spells")
_MONSTERS = _load("monsters")
_RULES = _load("rules")


def _fuzzy_get(query: str, table: dict[str, dict]) -> Optional[dict]:
    if not table:
        return None
    q = query.strip().lower()
    if q in table:
        return table[q]
    match = process.extractOne(q, list(table.keys()), scorer=fuzz.WRatio, score_cutoff=70)
    return table[match[0]] if match else None


def _api_lookup(category: str, query: str) -> Optional[dict]:
    if os.environ.get("CLAWDND_RULES_OFFLINE"):
        return None
    try:
        r = httpx.get(f"{_API_HOST}/api/{category}", params={"name": query}, timeout=8.0)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        detail = httpx.get(f"{_API_HOST}{results[0]['url']}", timeout=8.0)
        detail.raise_for_status()
        data = detail.json()
        data["_source"] = "dnd5eapi"
        return data
    except Exception:
        return None


def _find(table: dict[str, dict], query: str, api_category: str) -> Optional[dict]:
    hit = _fuzzy_get(query, table)
    if hit is not None:
        out = dict(hit)
        out["_source"] = "srd-bundled"
        return out
    return _api_lookup(api_category, query)


def find_condition(name: str) -> Optional[dict]:
    return _find(_CONDITIONS, name, "conditions")


def find_spell(name: str) -> Optional[dict]:
    return _find(_SPELLS, name, "spells")


def find_monster(name: str) -> Optional[dict]:
    return _find(_MONSTERS, name, "monsters")


def find_rule(name: str) -> Optional[dict]:
    return _find(_RULES, name, "rule-sections")


def search(query: str, category: Optional[str] = None) -> list[dict]:
    tables = {
        "spell": _SPELLS,
        "monster": _MONSTERS,
        "condition": _CONDITIONS,
        "rule": _RULES,
    }
    if category:
        tables = {category: tables.get(category, {})}
    q = query.strip().lower()
    out: list[dict] = []
    for cat, tbl in tables.items():
        for key, row in tbl.items():
            if q in key:
                out.append({"category": cat, "name": row["name"]})
    return out


def _wrap(result: Optional[dict], query: str) -> dict:
    return {"found": True, **result} if result else {"found": False, "query": query}


@mcp.tool()
def ping() -> str:
    """Health check. Returns ok and the bundled SRD dataset sizes."""
    return (
        f"clawdnd-rules: ok (v0.0.1) — bundled: {len(_CONDITIONS)} conditions, "
        f"{len(_SPELLS)} spells, {len(_MONSTERS)} monsters, {len(_RULES)} rules"
    )


@mcp.tool()
def lookup_condition(name: str) -> dict:
    """Look up a D&D 5e condition (e.g. 'prone', 'poisoned', 'grappled'). Fuzzy
    matching tolerates typos. Returns the SRD effect text."""
    return _wrap(find_condition(name), name)


@mcp.tool()
def lookup_spell(name: str) -> dict:
    """Look up a D&D 5e spell by name (fuzzy). Returns level, school, casting
    time, range, components, duration, classes, and description."""
    return _wrap(find_spell(name), name)


@mcp.tool()
def lookup_monster(name: str) -> dict:
    """Look up a D&D 5e monster/creature by name (fuzzy). Returns its stat block
    (AC, HP, abilities, actions, CR)."""
    return _wrap(find_monster(name), name)


@mcp.tool()
def lookup_rule(name: str) -> dict:
    """Look up a D&D 5e rule (e.g. 'advantage', 'cover', 'resting', 'death
    saving throws'). Fuzzy matching tolerates partial names."""
    return _wrap(find_rule(name), name)


@mcp.tool()
def search_srd(query: str, category: str = "") -> list[dict]:
    """Search the bundled SRD by substring across spells, monsters, conditions,
    and rules. Optionally restrict to a category: spell | monster | condition | rule."""
    return search(query, category or None)


if __name__ == "__main__":
    mcp.run()
