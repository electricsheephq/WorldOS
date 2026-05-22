"""SRD 5.2 progression tables — pure, cached accessors over data/srd/.

Loads progression.json, classes.json, spell_slots.json once and exposes the
lookups the character/leveling tools need: proficiency bonus, XP thresholds,
class hit dice/saves/skills, ASI levels, multiclass effective caster level, and
spell-slot tables. No campaign state here, so it's trivially unit-testable.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "data" / "srd"


@functools.lru_cache(maxsize=None)
def _load(name: str) -> dict:
    return json.loads((_DIR / f"{name}.json").read_text(encoding="utf-8"))


def progression() -> dict:
    return _load("progression")


def classes() -> dict:
    return _load("classes")


def _slots() -> dict:
    return _load("spell_slots")


def proficiency_bonus(total_level: int) -> int:
    lvl = max(1, min(20, total_level))
    return progression()["proficiency_bonus_by_level"][str(lvl)]


def xp_for_level(n: int) -> int:
    return progression()["xp_thresholds"][str(max(1, min(20, n)))]


def level_for_xp(xp: int) -> int:
    thresholds = progression()["xp_thresholds"]
    lvl = 1
    for n in range(1, 21):
        if xp >= thresholds[str(n)]:
            lvl = n
        else:
            break
    return lvl


def class_data(name: str) -> dict:
    c = classes().get(name.lower())
    if c is None:
        raise ValueError(f"unknown class {name!r}")
    return c


def hit_die(name: str) -> int:
    return class_data(name)["hit_die"]


def class_saves(name: str) -> list[str]:
    return class_data(name)["saves"]


def class_skills(name: str) -> dict:
    return class_data(name)["skills"]


def caster_type(name: str) -> str:
    return class_data(name)["caster_type"]


def multiclass_prereq(name: str) -> list[dict]:
    return class_data(name)["multiclass_prereq"]


def is_asi_level(class_name: str, class_level: int) -> bool:
    table = progression()["asi_levels"]
    return class_level in table.get(class_name.lower(), table["default"])


def average_hp(die: int) -> int:
    return progression()["average_hp_per_die"][str(die)]


def point_buy_cost() -> dict:
    return progression()["point_buy_cost"]


def standard_array() -> list[int]:
    return list(progression()["standard_array"])


def effective_caster_level(class_levels: list[tuple[str, int]]) -> int:
    """Multiclass effective caster level: full=+level, half=+level//2,
    third=+level//3 (per-class floor, summed). Pact/none contribute 0."""
    total = 0
    for name, level in class_levels:
        try:
            ct = caster_type(name)
        except ValueError:
            continue
        if ct == "full":
            total += level
        elif ct == "half":
            total += level // 2
        elif ct == "third":
            total += level // 3
    return total


def multiclass_slots(class_levels: list[tuple[str, int]]) -> dict[int, int]:
    cl = effective_caster_level(class_levels)
    if cl < 1:
        return {}
    arr = _slots()["multiclass"].get(str(min(20, cl)), [])
    return {i + 1: n for i, n in enumerate(arr)}


def warlock_pact_slots(warlock_level: int) -> dict | None:
    if warlock_level < 1:
        return None
    return _slots()["warlock_pact"].get(str(min(20, warlock_level)))
