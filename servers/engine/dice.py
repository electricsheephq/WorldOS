"""Deterministic, auditable D&D 5e dice.

Parses standard notation (NdM, +/- modifiers, khN/klN keep-highest/lowest) and
rolls d20 tests with advantage/disadvantage and natural-crit detection. Every
roll returns a structured, explainable result; an optional seed makes a roll
reproducible for tests and replay.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

_TERM = re.compile(r"(\d*)d(\d+)(kh\d+|kl\d+)?$")


@dataclass
class DiceRoll:
    expression: str
    total: int
    rolls: list[int]  # individual die faces counted toward the total (after keep/drop)
    dropped: list[int] = field(default_factory=list)
    modifier: int = 0
    detail: str = ""
    is_d20: bool = False
    natural: int | None = None  # the natural d20 face (for crit/fumble detection)
    crit: bool = False  # natural 20
    fumble: bool = False  # natural 1


def roll(
    expression: str,
    advantage: bool = False,
    disadvantage: bool = False,
    seed: int | None = None,
) -> DiceRoll:
    """Roll a dice expression. Advantage/disadvantage apply to a single d20 term
    and cancel each other out (5e rule) if both are set."""
    if advantage and disadvantage:
        advantage = disadvantage = False

    rng = random.Random(seed)
    expr = expression.replace(" ", "").lower().replace("d%", "d100")
    if not expr:
        raise ValueError("empty dice expression")

    terms = re.findall(r"[+-]?[^+-]+", expr)
    total = 0
    all_rolls: list[int] = []
    dropped: list[int] = []
    modifier = 0
    parts: list[str] = []
    is_d20 = False
    natural: int | None = None
    test_die_assigned = False

    for term in terms:
        sign = -1 if term.startswith("-") else 1
        body = term.lstrip("+-")
        m = _TERM.fullmatch(body)
        if m:
            n = int(m.group(1) or 1)
            sides = int(m.group(2))
            keep = m.group(3)
            if n == 0:
                raise ValueError(f"die count must be >= 1: {term!r}")
            faces = [rng.randint(1, sides) for _ in range(n)]
            kept = faces

            is_single_d20 = sides == 20 and n == 1
            if is_single_d20 and not test_die_assigned and (advantage or disadvantage):
                second = rng.randint(1, 20)
                pair = [faces[0], second]
                kept = [max(pair) if advantage else min(pair)]
                dropped.append(min(pair) if advantage else max(pair))
            elif keep:
                k = int(keep[2:])
                if k > n:
                    raise ValueError(f"cannot keep {k} of {n} dice: {term!r}")
                ordered = sorted(faces, reverse=keep.startswith("kh"))
                kept = ordered[:k]
                dropped.extend(ordered[k:])

            total += sign * sum(kept)
            all_rolls.extend(kept)
            # Only the FIRST single d20 is the test die (crit/fumble + advantage).
            if is_single_d20 and not test_die_assigned:
                is_d20 = True
                natural = kept[0]
                test_die_assigned = True
            parts.append(f"{term}{kept}")
        else:
            try:
                val = int(body)
            except ValueError as exc:
                raise ValueError(f"bad dice term: {term!r}") from exc
            total += sign * val
            modifier += sign * val
            parts.append(term)

    crit = bool(is_d20 and natural == 20)
    fumble = bool(is_d20 and natural == 1)
    detail = " ".join(parts) + f" = {total}"
    if is_d20 and advantage:
        detail += " (advantage)"
    elif is_d20 and disadvantage:
        detail += " (disadvantage)"

    return DiceRoll(
        expression=expression,
        total=total,
        rolls=all_rolls,
        dropped=dropped,
        modifier=modifier,
        detail=detail,
        is_d20=is_d20,
        natural=natural,
        crit=crit,
        fumble=fumble,
    )
