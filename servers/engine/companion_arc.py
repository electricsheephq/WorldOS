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
"""

from __future__ import annotations

from models import Campaign, Character

# Fraction of max HP at/below which a party member counts as "vulnerable" (also fires for
# a downed member at 0 HP). Drives the `party_vulnerable` agenda trigger.
_VULNERABLE_FRACTION = 0.25


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


def _agenda_triggered(character: Character, campaign: Campaign) -> bool:
    """Whether the companion's agenda condition currently holds (pure predicate)."""
    agenda = character.arc.agenda if character.arc else None
    if agenda is None:
        return False
    trigger = agenda.trigger
    if trigger == "attitude_below":
        return character.attitude_value < agenda.value
    if trigger == "day_reached":
        return campaign.day >= agenda.value
    if trigger == "party_vulnerable":
        return _party_vulnerable(campaign)
    if trigger == "prize_seized":
        return bool(campaign.flags.get("prize_seized"))
    return False


def evaluate(character: Character, campaign: Campaign) -> dict:
    """Advance ONE companion's arc against the current state (mutates in place).

    - Unlocks every still-locked `arc_gate` whose `threshold <= attitude_value` (the
      approval gauge), setting `unlocked=True`.
    - Fires the `agenda` if it hasn't already and its trigger holds, setting `fired=True`.

    Idempotent: a gate/agenda already resolved on a prior call is NOT reported again.
    Returns ``{newly_unlocked: [gate dicts], agenda_fired: bool, agenda: <dict|None>}`` —
    the moments that just became live, for the DM to dramatize. A character with no `arc`
    is a no-op (empty result)."""
    newly_unlocked: list[dict] = []
    agenda_fired = False
    agenda_dump = None

    arc = character.arc
    if arc is None:
        return {"newly_unlocked": newly_unlocked, "agenda_fired": agenda_fired, "agenda": agenda_dump}

    # Gates unlock when approval reaches their threshold — report each only on the
    # call that flips it (already-unlocked gates stay silent).
    for gate in arc.arc_gates:
        if not gate.unlocked and gate.threshold <= character.attitude_value:
            gate.unlocked = True
            newly_unlocked.append(gate.model_dump())

    # The sealed agenda fires once, when its trigger holds.
    agenda = arc.agenda
    if agenda is not None and not agenda.fired and _agenda_triggered(character, campaign):
        agenda.fired = True
        agenda_fired = True
        agenda_dump = agenda.model_dump()

    return {"newly_unlocked": newly_unlocked, "agenda_fired": agenda_fired, "agenda": agenda_dump}
