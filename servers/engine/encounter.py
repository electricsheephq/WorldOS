"""SRD 5e encounter-building and CR/XP math — pure, cached over data/srd/.

Implements the SRD encounter-difficulty workflow: convert a monster's Challenge
Rating to its XP value, sum the party's per-character XP thresholds into an
easy/medium/hard/deadly budget, apply the "encounter multiplier" for the number
of monsters, and classify the adjusted XP against the budget. No campaign state
or MCP here, so it's trivially unit-testable.

Threshold and CR->XP data live in data/srd/encounter_thresholds.json, loaded the
same way as srd_tables.py.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "data" / "srd"

DIFFICULTIES = ("easy", "medium", "hard", "deadly")


@functools.lru_cache(maxsize=None)
def _load(name: str) -> dict:
    return json.loads((_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _tables() -> dict:
    return _load("encounter_thresholds")


def _normalize_cr(cr: str | int | float) -> str:
    """Canonicalize a CR into a key of the cr_xp table.

    Accepts the SRD string forms ("0", "1/8", "1/4", "1/2", "1".."30") and
    numbers (int or float, e.g. 0.25 -> "1/4", 5 -> "5", 5.0 -> "5").
    """
    if isinstance(cr, str):
        return cr.strip()
    # numeric: map the fractional CRs, otherwise use the integer form.
    fractions = {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}
    if cr in fractions:
        return fractions[cr]
    if float(cr).is_integer():
        return str(int(cr))
    raise ValueError(f"unknown challenge rating {cr!r}")


def xp_for_cr(cr: str | int | float) -> int:
    """SRD CR -> XP value. Accepts "0"/"1/8"/"1/4"/"1/2"/"1".."30" or numbers."""
    key = _normalize_cr(cr)
    table = _tables()["cr_xp"]
    if key not in table:
        raise ValueError(f"unknown challenge rating {cr!r}")
    return table[key]


def xp_thresholds(party_levels: list[int]) -> dict:
    """Party XP budget: sum the per-character SRD thresholds for each PC level.

    Returns a dict with "easy"/"medium"/"hard"/"deadly" keys. Levels are clamped
    to the 1-20 table range.
    """
    by_level = _tables()["xp_thresholds_by_level"]
    totals = {d: 0 for d in DIFFICULTIES}
    for level in party_levels:
        lvl = max(1, min(20, int(level)))
        row = by_level[str(lvl)]
        for d in DIFFICULTIES:
            totals[d] += row[d]
    return totals


def encounter_multiplier(num_monsters: int) -> float:
    """SRD encounter multiplier by monster count.

    1 -> 1, 2 -> 1.5, 3-6 -> 2, 7-10 -> 2.5, 11-14 -> 3, 15+ -> 4.
    A count of 0 yields 1 (no monsters, no adjustment).
    """
    n = num_monsters
    if n <= 1:
        return 1.0
    if n == 2:
        return 1.5
    if n <= 6:
        return 2.0
    if n <= 10:
        return 2.5
    if n <= 14:
        return 3.0
    return 4.0


def adjusted_xp(monster_xps: list[int]) -> float:
    """Total monster XP scaled by the encounter multiplier for the group size."""
    return sum(monster_xps) * encounter_multiplier(len(monster_xps))


def encounter_difficulty(party_levels: list[int], monster_xps: list[int]) -> str:
    """Classify an encounter against the party's XP budget.

    adjusted = sum(monster_xps) * encounter_multiplier(len(monster_xps)).
    Returns "trivial" (below the easy threshold), else the highest band the
    adjusted XP meets or exceeds: "easy" | "medium" | "hard" | "deadly".
    """
    budget = xp_thresholds(party_levels)
    adjusted = adjusted_xp(monster_xps)
    if adjusted >= budget["deadly"]:
        return "deadly"
    if adjusted >= budget["hard"]:
        return "hard"
    if adjusted >= budget["medium"]:
        return "medium"
    if adjusted >= budget["easy"]:
        return "easy"
    return "trivial"
