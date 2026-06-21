"""Engine-run combat: the monster-AI contract (Track 2a — DESIGN STUB).

See docs/roadmap/engine-combat-loop-design.md for the full ADR. This module is the
load-bearing CONTRACT for the auto-sequencing combat loop; PR-A implements the
greedy-v1 policy. It is intentionally a STUB: no logic, no behavior, nothing imports
it yet (so it cannot change a single byte of live combat).

Posture (mirrors combat_grid.py): PURE — no Campaign mutation, no lock, no save, no
LLM, no I/O. `pick_action` is a deterministic decision over a read-only snapshot, so
the same state + same dice-seed always yields the same Intent. That purity is what
makes the engine-only combat smoke (qa/combat_smoke.py) reproducible and lets the AI
be unit-tested in isolation — feed a CombatView, assert the Intent — without standing
up a campaign. The MCP-facing loop in server.py is the SOLE WRITER: it translates an
Intent into the existing write verbs (attack / cast_spell / move_to_* / use_action /
next_turn), never a parallel resolution path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Optional

if TYPE_CHECKING:  # avoid any runtime import coupling — this module stays I/O-free
    from models import Character


# A monster/NPC's declared intent for its turn. The loop maps `kind` to one or more
# existing write verbs (a Multiattack re-asks pick_action per granted strike). DECLARATIVE
# only — it names WHAT the actor wants; it never mutates state. See ADR §2.
@dataclass(frozen=True)
class Intent:
    kind: Literal["attack", "cast", "move", "dash", "disengage", "dodge", "skip"]
    target_id: str = ""           # attack / single-target cast
    attack_name: str = ""         # scopes a Multiattack budget (server.py _attacker_multiattack_count)
    spell_name: str = ""          # cast
    to_cell: Optional[tuple[int, int]] = None  # move (grid / #461)
    to_zone: str = ""             # move (zone / S2.7)
    note: str = ""                # human-readable rationale for the digest / debugging


def pick_action(actor: "Character", combat_state: Any) -> Intent:
    """Choose the highest-expected-value action for a non-PC combatant's turn.

    DESIGN STUB — implemented in PR-A (greedy-v1 EV policy; see ADR §2). Pure and
    deterministic: reads only `actor` (read-only Character) and `combat_state` (a
    read-only CombatView snapshot the loop builds from the live Combat plus the
    actor's authoritative attack lines via server._monster_combat_entry). Returns an
    Intent; the loop is the sole writer that applies it.

    The v1 policy, in priority order: retreat-if-low → best in-reach attack
    (P(hit)*E[damage], focus-fire ties) → best cantrip/save-spell → move-to-reach →
    dodge/skip fallback. The Intent/`policy=` seam keeps a future BG3-tactical-v2
    additive.
    """
    raise NotImplementedError(
        "combat_ai.pick_action is a design stub (Track 2a). "
        "See docs/roadmap/engine-combat-loop-design.md §2; implemented in PR-A."
    )
