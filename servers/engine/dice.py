"""Deterministic, auditable D&D 5e dice.

Parses standard notation (NdM, +/- modifiers, khN/klN keep-highest/lowest) and
rolls d20 tests with advantage/disadvantage and natural-crit detection. Every
roll returns a structured, explainable result; an optional seed makes a roll
reproducible for tests and replay.
"""

from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass, field

_TERM = re.compile(r"(\d*)d(\d+)(kh\d+|kl\d+)?$")

# ── Process-level RNG (Track 2b — deterministic combat seed) ─────────────────────────
# A SINGLE module-level random.Random the engine draws from whenever roll() is called
# WITHOUT an explicit per-call `seed`. This is the correct way to make a *sequence* of
# rolls reproducible: a per-call seed would re-seed before every roll and collapse every
# draw to the same constant (a degenerate fight). One shared stream, seeded once, means
# "same seed -> same fight" while each individual roll is still a fresh draw.
#
# ADDITIVE / default-off: when WORLDOS_COMBAT_SEED is unset, the stream is seeded from
# random.Random(None) — i.e. OS entropy, byte-identical to today's non-deterministic
# random.Random(seed=None) behaviour. The env seed is a TEST affordance only (the TEST
# combat loop sets it); it does NOT need the sandbox guard because a deterministic seed in
# a live game is harmless — it only fixes WHICH dice come up, it never bends an outcome.
def _initial_seed() -> int | None:
    raw = os.environ.get("WORLDOS_COMBAT_SEED")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        # A non-integer seed is hashed to a stable int so any string still seeds reproducibly.
        return abs(hash(raw)) % (2**31)


_PROCESS_RNG = random.Random(_initial_seed())


def reseed_process_rng(seed: int | None) -> None:
    """Reseed the shared process-level dice stream (TEST affordance). Passing an int makes
    the subsequent sequence of un-seeded rolls reproducible; passing None reseeds from OS
    entropy. Used by the engine-only combat smoke / TEST loop to fix a whole fight. No effect
    on any roll that passes an explicit per-call `seed`."""
    _PROCESS_RNG.seed(seed)

# Bounds on a single dice term. Real D&D never needs more (a high-level fireball is
# ~20d6; d100 is the largest standard die), so these never affect legitimate rolls —
# they exist purely to stop a pathological expression (e.g. "100000000d20") from
# allocating a giant list and hanging the engine via the public `roll` MCP tool.
_MAX_DICE = 1000
_MAX_SIDES = 1000
_MAX_EXPRESSION_CHARS = 4096


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
    crit_min: int = 20,
) -> DiceRoll:
    """Roll a dice expression. Advantage/disadvantage apply to a single d20 term
    and cancel each other out (5e rule) if both are set.

    crit_min is the lowest natural d20 face that counts as a critical hit
    (default 20 = standard 5e). Champion fighters lower it via Improved/Superior
    Critical (19/18); callers thread the attacker's crit_min through here."""
    if advantage and disadvantage:
        advantage = disadvantage = False

    # An explicit per-call seed keeps its old meaning (a self-contained reproducible single
    # roll — used by unit tests). With NO per-call seed, draw from the shared process-level
    # stream so a *sequence* of rolls is reproducible under WORLDOS_COMBAT_SEED without every
    # roll collapsing to a constant. Unset env -> _PROCESS_RNG is OS-entropy seeded == today.
    rng = random.Random(seed) if seed is not None else _PROCESS_RNG
    expr = expression.replace(" ", "").lower().replace("d%", "d100")
    if not expr:
        raise ValueError("empty dice expression")
    if len(expr) > _MAX_EXPRESSION_CHARS:
        raise ValueError(f"dice expression must be <= {_MAX_EXPRESSION_CHARS} characters")

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
            if n > _MAX_DICE:
                raise ValueError(f"die count must be <= {_MAX_DICE}: {term!r}")
            if sides < 1 or sides > _MAX_SIDES:
                raise ValueError(f"die sides must be 1..{_MAX_SIDES}: {term!r}")
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

    crit = bool(is_d20 and natural is not None and natural >= crit_min)
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


def average_total(expression: str) -> int:
    """The deterministic EXPECTED average total of a dice expression, rounded to nearest int.

    Each NdM term contributes N*(M+1)/2 (the mean of N dM dice); flat modifiers add as-is.
    keep-highest/lowest (khN/klN) terms fall back to the per-die mean over the kept count
    (an approximation — the engine never crit-doubles a keep term, so this is only ever hit by
    a plain damage expression). Pure / I/O-free. Used by the TEST-only fast_resolve path so a
    sandbox fight resolves in a predictable number of rounds; never on a live game (guarded).

    Mirrors roll()'s term grammar exactly so the two stay in lockstep; raises the same
    ValueErrors on a malformed/oversized expression so fast_resolve can't smuggle a bad expr."""
    expr = expression.replace(" ", "").lower().replace("d%", "d100")
    if not expr:
        raise ValueError("empty dice expression")
    if len(expr) > _MAX_EXPRESSION_CHARS:
        raise ValueError(f"dice expression must be <= {_MAX_EXPRESSION_CHARS} characters")
    terms = re.findall(r"[+-]?[^+-]+", expr)
    total = 0.0
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
            if n > _MAX_DICE:
                raise ValueError(f"die count must be <= {_MAX_DICE}: {term!r}")
            if sides < 1 or sides > _MAX_SIDES:
                raise ValueError(f"die sides must be 1..{_MAX_SIDES}: {term!r}")
            per_die = (sides + 1) / 2.0
            count = n
            if keep:
                k = int(keep[2:])
                if k > n:
                    raise ValueError(f"cannot keep {k} of {n} dice: {term!r}")
                count = k  # approximate: mean per die over the kept count
            total += sign * per_die * count
        else:
            try:
                val = int(body)
            except ValueError as exc:
                raise ValueError(f"bad dice term: {term!r}") from exc
            total += sign * val
    return int(round(total))
