"""Faction-growth questlines (Quest & Arc engine, faction arcs / #127) — pure module.

The Skyrim/Kingmaker join->grow->lead loop, made a real engine state machine by GENERALIZING the
proven companion stage-machine (``CompanionQuestArc``) onto a FACTION-owned reputation/standing
gauge — NOT a parallel system. The companion arc is keyed to a companion and gates on its
``attitude_value``; a ``FactionArc`` is keyed to a ``faction_id`` and its stages gate on that
faction's ``reputation`` (the bidirectional trust gauge) or ``standing`` (the monotonic membership
gauge). Both are engine-mutated, so a gate reads ONLY engine values, NEVER fiction — the
questgen.py:7-19 discipline / invariant #3.

A THIN reuse, not a rebuild — every moving part already ships:

- the lifecycle enum is ``CompanionQuestStatus`` (``locked|available|active|resolved|failed``),
  reused verbatim;
- ``gate_value`` reads the faction gauge the same way ``events.trigger_holds`` reads it
  (reputation/standing only);
- ``apply_finale`` ripples a resolved stage's ``finale_effect`` through
  ``worldsim._apply_structured_effect`` — byte-for-byte the backlog/strategic/Event ripple path —
  with an ``effect_applied`` idempotency latch (a re-advance never double-ripples, exactly like a
  fired Event/Consequence).

Pure module (no MCP, no I/O), like ``events.py`` / ``worldsim.py`` / ``companion_arc.py``: the
functions read or mutate the in-memory ``Campaign`` in place; the engine stays the sole writer —
the MCP tool layer (``join_faction`` / ``advance_faction_arc`` in server.py) persists under
``campaign_lock`` + ``save_campaign``.
"""

from __future__ import annotations

import worldsim
from models import Campaign, Faction, FactionArc, FactionArcStage


def gauge_value(faction: Faction, gauge: str) -> int:
    """The CURRENT value of the gauge a stage gates on (engine-mutated; never fiction).

    ``"standing"`` -> the monotonic membership gauge; anything else (incl. the default
    ``"reputation"``) -> the bidirectional trust gauge. A faction always carries both as ints, so
    this never raises."""
    return faction.standing if gauge == "standing" else faction.reputation


def stage_gate_holds(stage: FactionArcStage, faction: Faction) -> bool:
    """Whether ``stage``'s gauge gate is satisfied NOW: the faction's gauge has reached
    ``unlock_at``.

    Pure + contract-safe — reads ONLY ``reputation`` / ``standing`` (engine-mutated), the same
    discipline as ``events.trigger_holds``. A non-negative threshold arms on a RISE to/above it
    (``gauge >= unlock_at``); a negative threshold arms on a FALL to/below it (``gauge <=
    unlock_at``) — the sign picks the direction, mirroring the Event ``reputation_at`` trigger.
    (``standing`` is non-negative so its gate is always the >= direction in practice.)"""
    val = gauge_value(faction, stage.gauge)
    return val <= stage.unlock_at if stage.unlock_at < 0 else val >= stage.unlock_at


def evaluate(arc: FactionArc, campaign: Campaign) -> dict:
    """Advance ``arc``'s stages against the CURRENT faction gauge (mutates) and report what just
    became live. The engine's deterministic, idempotent pass — mirrors ``companion_arc.evaluate``.

    A ``locked`` stage whose gate now holds flips to ``available`` (the rank-up unlock) — but ONLY
    when the arc is ARMED (the faction has ``joined`` it, unless the arc opts out via
    ``requires_joined=False``). A stage NEVER auto-advances past ``available`` here: moving to
    ``active``/``resolved``/``failed`` is an EXPLICIT ``advance_faction_arc`` call (the engine
    surfaces the opportunity; the DM/player drive the choice — the advise-not-act contract). The
    finale ripple is applied by ``apply_finale`` only on an explicit advance to ``resolved``.

    Idempotent: a stage already unlocked is not re-reported; re-evaluating the same snapshot
    yields the same result. Returns ``{"newly_available": [stage ids]}``."""
    faction = campaign.factions.get(arc.faction_id)
    if faction is None:
        return {"newly_available": []}  # a dangling arc (faction removed) degrades to inert
    armed = faction.joined or not arc.requires_joined
    newly: list[str] = []
    if armed and arc.status == "locked":
        arc.status = "available"  # the arc itself opens once armed
    for stage in arc.stages:
        if stage.status == "locked" and armed and stage_gate_holds(stage, faction):
            stage.status = "available"
            newly.append(stage.id)
    return {"newly_available": newly}


def detect_rank_available(campaign: Campaign) -> list[dict]:
    """ADVISORY read-only detector: for each ARMED faction arc, the stages that are ``available``
    (a rank-up the player has earned but not yet taken) or ``active`` (a questline beat in flight).

    Mirrors the scene_debt / Director contract: it DETECTS opportunity from engine state and
    returns it; it NEVER advances an arc or mutates anything. The DM reads the nudge and chooses
    whether to play the beat (the engine must not auto-advance a faction quest — map seam #5). Pure
    (no mutation): unlike ``evaluate`` it does not flip ``locked->available``; it only reports
    stages already in those states, so calling it is side-effect-free.

    Returns one entry per faction arc with available/active stages, deterministically ordered by
    arc id then stage author order."""
    out: list[dict] = []
    for arc in sorted(campaign.faction_arcs.values(), key=lambda a: a.id):
        faction = campaign.factions.get(arc.faction_id)
        if faction is None:
            continue
        if arc.requires_joined and not faction.joined:
            continue  # not a member yet — no rank-up to nudge
        available = [s.id for s in arc.stages if s.status == "available"]
        active = [s.id for s in arc.stages if s.status == "active"]
        if not available and not active:
            continue
        out.append(
            {
                "arc_id": arc.id,
                "faction_id": arc.faction_id,
                "faction_name": faction.name,
                "title": arc.title,
                "available_stage_ids": available,
                "active_stage_ids": active,
                # A terse DM-facing nudge — "rank-up available" vs "questline in flight".
                "nudge": (
                    f"faction questline '{arc.title}': rank-up available"
                    if available
                    else f"faction questline '{arc.title}': stage in flight"
                ),
            }
        )
    return out


def apply_finale(campaign: Campaign, stage: FactionArcStage) -> dict | None:
    """Apply a resolved stage's world-changing ``finale_effect`` ONCE (mutates) and return a
    DM-facing summary of what moved, or ``None`` if there is nothing to apply / it already fired.

    DETERMINISTIC — no LLM, no roll. The ripple goes through ``worldsim._apply_structured_effect``,
    byte-for-byte the path the backlog / strategic board / Layer-3 Events use, so a faction finale
    moves the world through the EXACT same vocabulary (set a flag, shift a faction's reputation, a
    control/arrival marker). Plus the same three thin extension keys an Event ``Outcome`` carries
    (``decision_flag`` -> ``flags``; ``schedule_in_days``/``schedule_text`` -> a follow-on
    Consequence) so a finale can arm a companion flip or leave a lingering echo — the join->lead
    payoff rippling outward exactly like a Kingmaker decisional.

    IDEMPOTENT: guarded by ``stage.effect_applied`` — a second call (a re-advance to ``resolved``)
    is a pure no-op, so the finale ripples at most once even if the tool is called twice."""
    effect = stage.finale_effect
    if effect is None or stage.effect_applied:
        return None

    # The shared ripple — the exact engine path the living world uses. `narrate` is the fallback.
    shared = {
        "flag": effect.flag,
        "faction_id": effect.faction_id,
        "reputation_delta": effect.reputation_delta,
        "controller_id": effect.controller_id,
        "location_id": effect.location_id,
        "npc_name": effect.npc_name,
    }
    narrated = worldsim._apply_structured_effect(campaign, shared, fallback=effect.narrate)

    flags_set: list[str] = []
    if effect.flag:
        flags_set.append(effect.flag)

    rep_shift: dict | None = None
    if effect.faction_id and effect.faction_id in campaign.factions:
        rep_shift = {
            "faction_id": effect.faction_id,
            "reputation_delta": effect.reputation_delta,
            "reputation": campaign.factions[effect.faction_id].reputation,
        }

    # Extension key 1 — the L2<->L3 seam: a finale may arm a companion flip (the same store as
    # record_decision(sets_flag=)). E.g. seizing leadership of the Fist turns a rival-companion.
    decision_flag = (effect.decision_flag or "").strip()
    if decision_flag:
        campaign.flags[decision_flag] = True
        if decision_flag not in flags_set:
            flags_set.append(decision_flag)

    # Extension key 2 — the rule-of-three echo: a finale may leave a lingering Consequence. Both a
    # positive day count AND text are required (a half-spec is a no-op, never a 0-day phantom).
    scheduled: dict | None = None
    if effect.schedule_in_days > 0 and effect.schedule_text.strip():
        import consequences as consequences_mod

        conseq = consequences_mod.schedule(
            campaign,
            effect.schedule_in_days,
            effect.schedule_text.strip(),
            note=f"faction_arc:{stage.id}",
        )
        scheduled = {"trigger_day": conseq.trigger_day, "text": conseq.text}

    # Idempotency latch — the finale ripples at most once.
    stage.effect_applied = True

    return {
        "stage_id": stage.id,
        "narrated_line": narrated,
        "flags_set": flags_set,
        "rep_shift": rep_shift,
        "scheduled": scheduled,
        "decision_flag": decision_flag,
    }
