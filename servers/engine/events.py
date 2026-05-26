"""First-class stumble-into Events (Quest & Arc engine, Layer 3) — pure module.

The Kingmaker "stumble-into" decisional, made a real engine fact: a content-authored
``Event`` whose ``ParleyOption``s each carry a DETERMINISTIC ``Outcome``. Picking an option
ripples through the EXISTING engine vocabulary and can STAGE the already-merged Layer-2
companion flip — Layer 3 sets a ``decision_flag``; Layer 2 (shipped) reads it. They meet at
``Campaign.flags``, the contract-safe engine-mutated store.

A THIN wrapper over machinery that already ships — no new resolver, no new state machine:

- ``present`` returns the unresolved Events whose contract-safe ``trigger`` holds NOW (reads
  ONLY flags / faction reputation / day — never fiction, the questgen.py:7-19 discipline).
  Idempotent: a ``resolved`` Event is skipped, exactly like ``consequences.due`` skips a fired
  Consequence. The Director surfaces these as a soft nudge.
- ``resolve`` applies the chosen option's ``Outcome``: the shared keys via
  ``worldsim._apply_structured_effect`` (byte-for-byte the backlog/strategic ripple path), then
  the three thin extension keys inline (``decision_flag`` -> ``flags``; ``schedule_in_days`` /
  ``schedule_text`` -> ``consequences.schedule``), then marks the Event ``resolved``. IDEMPOTENT:
  re-resolving a ``resolved`` Event is a pure no-op (applies NOTHING).

Pure module (no MCP, no I/O), like ``worldsim.py`` / ``consequences.py`` / ``companion_arc.py``:
the functions mutate the in-memory ``Campaign`` in place; the engine stays the sole writer — the
MCP tool layer (``present_events`` / ``resolve_event``) persists under ``campaign_lock`` +
``save_campaign``.
"""

from __future__ import annotations

import consequences as consequences_mod
import worldsim
from models import Campaign, Event, ParleyOption


def trigger_holds(event: Event, campaign: Campaign) -> bool:
    """Whether ``event``'s trigger is satisfied NOW (contract-safe: reads ONLY engine-mutated
    flags / faction reputation / day — never fiction).

    - ``manual``: always available (the DM/content drops it; the stumble-into default).
    - ``flag_set``: ``campaign.flags.get(event.trigger_value)`` is True.
    - ``day_reached``: ``campaign.day >= event.trigger_threshold``.
    - ``reputation_at``: the named faction's reputation has reached ``trigger_threshold`` — the
      SIGN of the threshold picks the direction (>= for a non-negative target, <= for a negative
      one), so a negative target arms when reputation has fallen TO/BELOW it. A faction that no
      longer exists never satisfies the trigger (degrade, never raise).

    An unknown/garbage trigger is treated as unavailable (never raises) — a malformed seeded
    Event degrades to "never surfaces" rather than breaking the read."""
    trig = event.trigger
    if trig == "manual":
        return True
    if trig == "flag_set":
        return bool(event.trigger_value) and bool(campaign.flags.get(event.trigger_value))
    if trig == "day_reached":
        return campaign.day >= event.trigger_threshold
    if trig == "reputation_at":
        fac = campaign.factions.get(event.trigger_faction_id)
        if fac is None:
            return False
        target = event.trigger_threshold
        # Sign of the target picks the direction: a negative target arms on a FALL to/below it
        # (reputation <= target); a non-negative target arms on a RISE to/above it.
        return fac.reputation <= target if target < 0 else fac.reputation >= target
    return False


def present(campaign: Campaign) -> list[Event]:
    """Return the UNRESOLVED Events whose trigger holds now (read-only; does NOT mutate).

    Skips ``resolved`` Events (idempotent — a fired Event never re-presents, like
    ``consequences.due``). Deterministic order (by event id) so the surface is reproducible
    regardless of dict insertion order."""
    return [
        ev
        for ev in sorted(campaign.events.values(), key=lambda e: e.id)
        if not ev.resolved and trigger_holds(ev, campaign)
    ]


def _outcome_shared_effect(option: ParleyOption) -> dict:
    """Project the chosen option's Outcome onto the dict ``worldsim._apply_structured_effect``
    consumes — the keys it shares with the backlog/strategic ripple path, byte-for-byte. The
    three extension keys (decision_flag / schedule_* / narrate) are handled separately in
    ``resolve`` and are deliberately NOT in this dict (unknown keys are harmless there, but we
    keep the contract crisp)."""
    o = option.outcome
    return {
        "flag": o.flag,
        "faction_id": o.faction_id,
        "reputation_delta": o.reputation_delta,
        "controller_id": o.controller_id,
        "location_id": o.location_id,
        "npc_name": o.npc_name,
    }


def find_option(event: Event, option_label: str) -> ParleyOption | None:
    """The ParleyOption whose ``label`` matches (case-insensitive, trimmed), or None.

    Picks the FIRST match in author order so a content author's option ordering is honored."""
    want = (option_label or "").strip().casefold()
    for opt in event.options:
        if opt.label.strip().casefold() == want:
            return opt
    return None


def resolve(campaign: Campaign, event: Event, option: ParleyOption) -> dict:
    """Apply ``option``'s deterministic Outcome to ``campaign`` (mutates) and mark ``event``
    resolved. Returns a DM-facing summary of what moved. DETERMINISTIC — no LLM, no roll here.

    Order: the shared ripple via ``worldsim._apply_structured_effect`` (flag / reputation /
    control / arrival), then the three extension keys inline:
      * ``decision_flag`` -> ``campaign.flags[decision_flag] = True`` (the L2<->L3 seam — arms a
        matching ``attitude_below`` CompanionAgenda, identical to ``record_decision(sets_flag=)``).
      * ``schedule_in_days`` / ``schedule_text`` -> ``consequences.schedule`` (the rule-of-three
        echo) — only when BOTH a positive day count and text are present.
    Finally ``event.resolved = True`` (idempotency latch).

    NOTE: this assumes the caller has already short-circuited a re-resolve of a ``resolved``
    Event (see ``resolve_event`` in server.py) — it does not re-check ``resolved`` itself, so the
    pure function stays a single deterministic application step."""
    o = option.outcome

    # The shared ripple — the exact path the backlog/strategic board uses. `narrate` is the
    # DM-facing fallback line.
    narrated = worldsim._apply_structured_effect(
        campaign, _outcome_shared_effect(option), fallback=o.narrate
    )

    flags_set: list[str] = []
    if o.flag:
        flags_set.append(o.flag)

    rep_shift: dict | None = None
    if o.faction_id and o.faction_id in campaign.factions:
        # Report the post-shift reputation the effect just applied (clamped by the effect path).
        rep_shift = {
            "faction_id": o.faction_id,
            "reputation_delta": o.reputation_delta,
            "reputation": campaign.factions[o.faction_id].reputation,
        }

    # Extension key 1 — the L2<->L3 seam: set the content-defined decision flag (same store as
    # record_decision(sets_flag=)). Arms any attitude_below agenda whose decision_flag matches.
    decision_flag = (o.decision_flag or "").strip()
    if decision_flag:
        campaign.flags[decision_flag] = True
        if decision_flag not in flags_set:
            flags_set.append(decision_flag)

    # Extension key 2 — the rule-of-three echo: schedule a follow-on Consequence so the choice
    # lingers/returns. Requires BOTH a positive day count and text (a malformed half-spec is a
    # no-op, never a 0-day phantom consequence).
    scheduled: dict | None = None
    if o.schedule_in_days > 0 and o.schedule_text.strip():
        conseq = consequences_mod.schedule(
            campaign,
            o.schedule_in_days,
            o.schedule_text.strip(),
            note=f"event:{event.id}",
        )
        scheduled = {"trigger_day": conseq.trigger_day, "text": conseq.text}

    # Idempotency latch — a fired Event never re-presents or re-applies.
    event.resolved = True

    return {
        "event_id": event.id,
        "option_label": option.label,
        "narrated_line": narrated,
        "flags_set": flags_set,
        "rep_shift": rep_shift,
        "scheduled": scheduled,
        "decision_flag": decision_flag,
        "resolved": True,
    }
