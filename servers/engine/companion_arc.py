"""Companion relationship-arc + agenda engine (S4) — pure module, mirrors `worldsim.py`.

A QA betrayal scene exposed the gap: a saboteur companion's agenda lived only in an
ephemeral QA prompt string, so a "turn on the party" beat didn't reliably fire and a
companion's loyalty/quest never lingered (the story rubric's recurring "nothing lingers
/ companion snaps to neutral" defect). This module makes a companion's arc a REAL,
engine-evaluated event built on the EXISTING approval gauge (`Character.attitude_value`):

- ``evaluate`` UNLOCKS any `arc_gate` whose `threshold` the companion's approval has
  reached (mutating `unlocked=True`), and FIRES the `agenda` when its trigger holds
  (mutating `fired=True`). It reports each unlock/fire EXACTLY ONCE — an already-unlocked
  gate or already-fired agenda is silent on the next call. The DM dramatizes a fired
  gate/agenda (a betrayal fires as a real `attack`, never narration); the engine just
  decides *whether the moment has arrived*.

Pure module (no MCP, no I/O), like `worldsim.py`/`consequences.py`: ``evaluate`` mutates
the in-memory `Character`/`Campaign` in place and is idempotent. The engine stays the sole
writer — the MCP tool layer (`check_companion_arc`) persists under `campaign_lock` +
`save_campaign`.

``attitude_below`` betrayal probability curve (issue #142)
----------------------------------------------------------
When a companion's `attitude_value` is >= the threshold `value`, P(snap) = 0 — the agenda
never fires while the relationship is still above the breaking point.

When attitude_value < value the per-beat snap probability rises linearly with how far below
the threshold the companion has fallen:

    gap   = value - attitude_value          # strictly positive once below threshold
    span  = value - ATTITUDE_SNAP_FLOOR     # full range from threshold down to the floor

    raw_p = gap / span                      # 0.0 at threshold, 1.0 at floor

    p     = min(raw_p, ATTITUDE_SNAP_MAX)   # clamped so even the worst relationship
                                            # only reaches ATTITUDE_SNAP_MAX per beat

    fire? = rng.random() < p               # sampled each call until the agenda fires

ATTITUDE_SNAP_FLOOR (-100) is the lowest plausible attitude (deep contempt / total
betrayal) — the denominator anchors the curve against the approval scale instead of
making the slope an arbitrary magic number. ATTITUDE_SNAP_MAX (0.35) caps each beat so
the agenda can't fire on the very first call below threshold (typical gap at threshold
crossing ≈ 1 → raw_p < 0.01) and a companion sitting at deep-red (gap ≈ span) still
only snaps ~35% of the time per beat — it feels dangerous, not instant. When the party
is vulnerable the probability is nudged up by ATTITUDE_SNAP_VULNERABLE_BONUS (0.10) so
the betrayer is more likely to pick the worst moment.

The other triggers (day_reached, prize_seized, party_vulnerable) are SPECIFIC EVENTS —
they either happened or they didn't; making them probabilistic would break their semantics.
"""

from __future__ import annotations

import random

from models import Campaign, Character

# Fraction of max HP at/below which a party member counts as "vulnerable" (also fires for
# a downed member at 0 HP). Drives the `party_vulnerable` agenda trigger.
_VULNERABLE_FRACTION = 0.25

# --- attitude_below probability curve constants (issue #142) -------------------

# Lower anchor of the approval scale — attitudes rarely go below -100 in practice.
ATTITUDE_SNAP_FLOOR: int = -100

# Maximum per-beat snap probability regardless of how far below the threshold the
# companion's attitude has fallen — keeps the betrayal a "rising chance", not a coin flip.
ATTITUDE_SNAP_MAX: float = 0.35

# Additive bonus to the snap probability when _party_vulnerable() is True, so the
# saboteur is more likely to strike when the party is weakest.
ATTITUDE_SNAP_VULNERABLE_BONUS: float = 0.10


def _party_vulnerable(campaign: Campaign) -> bool:
    """True if any PARTY member (PC or companion) is downed or below the HP threshold —
    a wounded, scattered party is the moment a saboteur strikes. Reads the campaign's
    `party` roster so off-screen NPCs/monsters never count."""
    for cid in campaign.party:
        member = campaign.characters.get(cid)
        if member is None:
            continue
        if member.current_hp <= 0:
            return True
        if member.current_hp <= _VULNERABLE_FRACTION * member.max_hp:
            return True
    return False


def _attitude_below_snap_p(attitude_value: int, threshold: int, vulnerable: bool) -> float:
    """Per-beat snap probability for an ``attitude_below`` agenda (issue #142).

    Returns 0.0 when attitude_value >= threshold (never fires above the line).
    Otherwise rises linearly from ~0 at the threshold to ATTITUDE_SNAP_MAX at or below
    ATTITUDE_SNAP_FLOOR, optionally boosted by ATTITUDE_SNAP_VULNERABLE_BONUS when
    the party is weak. The result is clamped to [0.0, ATTITUDE_SNAP_MAX +
    ATTITUDE_SNAP_VULNERABLE_BONUS] — never > ~0.45 so even a companion deep in the
    red still requires multiple beats to reliably snap."""
    if attitude_value >= threshold:
        return 0.0
    span = threshold - ATTITUDE_SNAP_FLOOR  # > 0: threshold is always > floor
    gap = threshold - attitude_value         # > 0: attitude is below threshold
    raw_p = gap / span
    p = min(raw_p, ATTITUDE_SNAP_MAX)
    if vulnerable:
        p = min(p + ATTITUDE_SNAP_VULNERABLE_BONUS, ATTITUDE_SNAP_MAX + ATTITUDE_SNAP_VULNERABLE_BONUS)
    return p


def _agenda_triggered(character: Character, campaign: Campaign, rng: random.Random | None = None) -> bool:
    """Whether the companion's agenda fires this beat.

    For ``attitude_below``: probabilistic (rising chance as attitude drops — see module
    docstring). A fresh ``random.Random()`` is used when no ``rng`` is passed; pass a
    seeded one for deterministic tests.

    For all other triggers: deterministic (the event either occurred or it didn't).
    """
    agenda = character.arc.agenda if character.arc else None
    if agenda is None:
        return False
    trigger = agenda.trigger
    if trigger == "attitude_below":
        # value is required for this trigger (model validator), but guard None defensively
        # so a hand-built/legacy agenda can never raise on the comparison.
        if agenda.value is None:
            return False
        p = _attitude_below_snap_p(
            character.attitude_value,
            agenda.value,
            _party_vulnerable(campaign),
        )
        if p <= 0.0:
            return False
        r = rng if rng is not None else random.Random()
        return r.random() < p
    if trigger == "day_reached":
        return agenda.value is not None and campaign.day >= agenda.value
    if trigger == "party_vulnerable":
        return _party_vulnerable(campaign)
    if trigger == "prize_seized":
        return bool(campaign.flags.get("prize_seized"))
    return False


def _unlock_companion_quest_arc(character: Character, campaign: Campaign, gate) -> dict | None:
    """Mark a linked companion quest arc/stage available for a personal_quest gate.

    This is deliberately narrow: the relationship gate can surface availability once,
    but success/failure stays behind the explicit companion quest advancement API."""
    if gate.kind != "personal_quest" or not gate.quest_arc_id:
        return None

    event = {
        "quest_arc_id": gate.quest_arc_id,
        "stage_id": gate.stage_id,
        "status": "",
    }
    arc = campaign.companion_quest_arcs.get(gate.quest_arc_id)
    if arc is None:
        event["error"] = f"no companion quest arc {gate.quest_arc_id!r}"
        return event
    if arc.companion_id and arc.companion_id != character.id:
        event["error"] = (
            f"companion quest arc {gate.quest_arc_id!r} belongs to "
            f"{arc.companion_id!r}, not {character.id!r}"
        )
        return event

    stage = None
    if gate.stage_id:
        stage = next((s for s in arc.stages if s.id == gate.stage_id), None)
        if stage is None:
            event["error"] = f"no stage {gate.stage_id!r} in companion quest arc {gate.quest_arc_id!r}"
            return event

    changed: list[str] = []
    if arc.status == "locked":
        arc.status = "available"
        changed.append("arc")
    if stage is not None and stage.status == "locked":
        stage.status = "available"
        changed.append("stage")
    if not changed:
        event["status"] = arc.status
        if stage is not None:
            event["stage_status"] = stage.status
        event["no_transition"] = True
        return event
    event["status"] = arc.status
    if stage is not None:
        event["stage_status"] = stage.status
    event["changed"] = changed
    return event


def evaluate(character: Character, campaign: Campaign, rng: random.Random | None = None) -> dict:
    """Advance ONE companion's arc against the current state (mutates in place).

    - Unlocks every still-locked `arc_gate` whose `threshold <= attitude_value` (the
      approval gauge), setting `unlocked=True`.
    - Fires the `agenda` if it hasn't already and its trigger holds, setting `fired=True`.
      For an ``attitude_below`` agenda the trigger is probabilistic (rising chance per
      beat — see module docstring); pass a seeded ``rng`` for deterministic tests.

    Idempotent: a gate/agenda already resolved on a prior call is NOT reported again.
    Returns ``{newly_unlocked: [gate dicts], agenda_fired: bool, agenda: <dict|None>}`` —
    the moments that just became live, for the DM to dramatize. A character with no `arc`
    is a no-op (empty result)."""
    newly_unlocked: list[dict] = []
    companion_quest_unlocks: list[dict] = []
    agenda_fired = False
    agenda_dump = None

    arc = character.arc
    if arc is None:
        return {"newly_unlocked": newly_unlocked, "agenda_fired": agenda_fired, "agenda": agenda_dump}

    # Gates unlock when approval reaches their threshold — report each only on the
    # call that flips it (already-unlocked gates stay silent).
    for gate in arc.arc_gates:
        if not gate.unlocked and gate.threshold <= character.attitude_value:
            quest_unlock = _unlock_companion_quest_arc(character, campaign, gate)
            if quest_unlock is not None and quest_unlock.get("error"):
                companion_quest_unlocks.append(quest_unlock)
                continue
            gate.unlocked = True
            newly_unlocked.append(gate.model_dump())
            if quest_unlock is not None:
                companion_quest_unlocks.append(quest_unlock)

    # The sealed agenda fires once, when its trigger holds.
    agenda = arc.agenda
    if agenda is not None and not agenda.fired and _agenda_triggered(character, campaign, rng=rng):
        agenda.fired = True
        agenda_fired = True
        agenda_dump = agenda.model_dump()

    out = {"newly_unlocked": newly_unlocked, "agenda_fired": agenda_fired, "agenda": agenda_dump}
    if companion_quest_unlocks:
        out["companion_quest_unlocks"] = companion_quest_unlocks
    return out
