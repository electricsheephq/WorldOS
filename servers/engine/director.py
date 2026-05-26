"""Campaign Director — advisory ranking layer (issue #72).

ADVISORY ONLY. This module consumes detected scene-debts and returns a prioritised
advisory dict the DM reads at beat-start. It NEVER acts on debts, mutates state,
or makes narrative-quality judgments. The DM reads + chooses; resolution is always
an EXPLICIT tool call (resolve_scene_debt), never automatic.

Pure (no I/O): input is a Campaign snapshot, output is a serialisable dict.
"""

from __future__ import annotations

from models import Campaign, SceneDebt
import scene_debt as _sd

# Severity order for ranking (high first)
_SEV_RANK: dict[str, int] = {"high": 0, "med": 1, "low": 2}

# How many debts to surface in the top advisory (cap keeps DM from being overwhelmed)
_TOP_N: int = 3


# ── One-line advisory nudges per debt kind ────────────────────────────────────

def _nudge(debt: SceneDebt) -> str:
    """Return a one-line DM-facing advisory nudge for a debt."""
    ev = debt.evidence or {}

    if debt.kind == "hook_untracked":
        title = ev.get("hook_title") or ev.get("hook_id", "this hook")
        return f"Untracked hook '{title}' — call add_quest to promote it into a tracked quest."

    if debt.kind == "quest_stalled":
        title = ev.get("quest_title") or ev.get("quest_id", "this quest")
        return (
            f"Quest '{title}' has stalled — weave an advancement beat or decision to move it forward."
        )

    if debt.kind == "choice_without_outcome":
        summary = ev.get("summary") or debt.subject
        return (
            f"Decision '{summary[:60]}' was offered but never resolved — "
            f"record the party's choice with update_decision or re-open the scene."
        )

    if debt.kind == "due_consequence":
        days = ev.get("overdue_days", 0)
        note = ev.get("note") or ev.get("consequence_id", "")
        ago = f" ({days}d overdue)" if days else ""
        return f"Consequence{ago} is due — call check_consequences to surface it: '{str(note)[:60]}'."

    if debt.kind == "thread_pressure":
        tid = ev.get("thread_id") or ev.get("consequence_id", "")
        return (
            f"Standing thread '{tid}' world-beat is overdue — call world_tick to fire it."
        )

    if debt.kind == "npc_introduced_silent":
        name = ev.get("name") or debt.subject
        return (
            f"NPC '{name}' has been introduced but hasn't spoken — "
            f"give them a line or record their first memory with remember."
        )

    # fallback
    return f"{debt.kind}: {debt.detail[:80]}"


# ── Public API ────────────────────────────────────────────────────────────────


def compute(c: Campaign) -> dict:
    """Detect scene-debts and return a prioritised advisory for the DM.

    Read-only. Returns::

        {
            "debts": [<top 3 SceneDebt dicts, highest severity first>],
            "advisory": ["one-line nudge per debt"],
            "total_debts": <int>,
        }

    An empty ``debts`` list means no structural debts detected — today's
    behavior. The DM consults this at beat-start and weaves the top 1-2 nudges
    organically; it is NOT a mandatory checklist.

    Advisory contract: the engine detects + advises; the DM decides + acts.
    Resolution is EXPLICIT via resolve_scene_debt, never automatic.
    """
    all_debts = _sd.detect(c)
    # Rank: high → med → low; stable (preserves detection order within a tier)
    ranked = sorted(all_debts, key=lambda d: (_SEV_RANK.get(d.severity, 9), 0))
    top = ranked[:_TOP_N]
    return {
        "debts": [d.model_dump() for d in top],
        "advisory": [_nudge(d) for d in top],
        "total_debts": len(all_debts),
    }
