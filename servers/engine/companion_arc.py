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

Decision-gated escalation (Quest & Arc engine, Layer 2)
-------------------------------------------------------
A recorded player CHOICE can RAISE the betrayal weight: when an ``attitude_below``
agenda carries a ``decision_flag`` and that CONTENT-defined flag is present+True in
``Campaign.flags`` (set when the gating choice was made — "let the daughter die",
"took the bribe"), the per-beat snap probability is boosted by
ATTITUDE_SNAP_DECISION_BONUS (0.30), capped at ATTITUDE_SNAP_DECISION_MAX (0.90). The
turn becomes far likelier, but it is still ROLLED and still requires the relationship to
be below the breaking point first — the companion stays in-character; the choice tips an
already-curdling bond over, it doesn't conjure a betrayal from nowhere. The boost reads
only the engine-mutated `flags` dict, never fiction (contract-safe). Empty
`decision_flag` (or the flag absent/False) == the unescalated curve above, byte-for-byte.

The other triggers (day_reached, prize_seized, party_vulnerable) are SPECIFIC EVENTS —
they either happened or they didn't; making them probabilistic would break their semantics.
The decision_flag boost is deliberately scoped to ``attitude_below`` only (the rising-
chance trigger); the event triggers ignore it.

Warning bands (telegraph, not surprise-from-nowhere)
----------------------------------------------------
``evaluate`` surfaces an advisory ``betrayal_warning`` when a companion carrying a LIVE
(unfired) ``attitude_below`` agenda sits in the danger band ATTITUDE_WARN_HIGH (-20) ..
ATTITUDE_WARN_LOW (-40) — so the DM/Director can foreshadow the fracture before it fires.
It is ADVISORY ONLY: it never fires the agenda, never mutates state, and reads only the
attitude gauge + the (engine-set) decision_flag. Outside the band there is no warning.
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

# --- decision-gated escalation constants (Quest & Arc engine, Layer 2) ----------

# Additive boost to the per-beat snap probability when the agenda's `decision_flag` is
# set+True in Campaign.flags — a recorded player CHOICE that tips an already-curdling
# bond over ("let the daughter die → the knight turns"). Meaningful (the "betrayal
# chance spikes"), not a guarantee.
ATTITUDE_SNAP_DECISION_BONUS: float = 0.30

# Hard ceiling on the snap probability ONCE the decision boost is applied — the choice
# makes the turn far likelier but never a certainty per beat (the companion stays
# in-character; the betrayal is still rolled and still staged at an event by the DM).
ATTITUDE_SNAP_DECISION_MAX: float = 0.90

# --- warning-band constants (telegraph, Quest & Arc engine, Layer 2) ------------

# The attitude danger band for a LIVE attitude_below agenda: when attitude_value sits
# in [ATTITUDE_WARN_LOW, ATTITUDE_WARN_HIGH] the relationship is fracturing but hasn't
# bottomed out — `evaluate` surfaces an advisory so the DM/Director can foreshadow.
ATTITUDE_WARN_HIGH: int = -20  # upper edge: the bond has clearly soured
ATTITUDE_WARN_LOW: int = -40   # lower edge: below this it's already deep-red / near-snap


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


def _attitude_below_snap_p(
    attitude_value: int,
    threshold: int,
    vulnerable: bool,
    decision_flag_active: bool = False,
) -> float:
    """Per-beat snap probability for an ``attitude_below`` agenda (issue #142).

    Returns 0.0 when attitude_value >= threshold (never fires above the line) — the
    decision boost NEVER overrides this, so a recorded choice can't fire a betrayal
    while the relationship is still above its breaking point.

    Otherwise rises linearly from ~0 at the threshold to ATTITUDE_SNAP_MAX at or below
    ATTITUDE_SNAP_FLOOR, optionally boosted by ATTITUDE_SNAP_VULNERABLE_BONUS when
    the party is weak. The base+vulnerable result is clamped to [0.0, ATTITUDE_SNAP_MAX +
    ATTITUDE_SNAP_VULNERABLE_BONUS] — never > ~0.45 so even a companion deep in the
    red still requires multiple beats to reliably snap.

    Decision-gated escalation (Layer 2): when ``decision_flag_active`` (a recorded
    player CHOICE set the agenda's content-defined flag), ATTITUDE_SNAP_DECISION_BONUS
    is ADDED on top and the whole thing re-capped at ATTITUDE_SNAP_DECISION_MAX (0.90)
    — the betrayal chance spikes, but it is still rolled, never certain, and never above
    the breaking-point guard."""
    if attitude_value >= threshold:
        return 0.0
    span = threshold - ATTITUDE_SNAP_FLOOR  # > 0: threshold is always > floor
    gap = threshold - attitude_value         # > 0: attitude is below threshold
    raw_p = gap / span
    p = min(raw_p, ATTITUDE_SNAP_MAX)
    if vulnerable:
        p = min(p + ATTITUDE_SNAP_VULNERABLE_BONUS, ATTITUDE_SNAP_MAX + ATTITUDE_SNAP_VULNERABLE_BONUS)
    if decision_flag_active:
        # The recorded choice tips the bond over: spike the chance, but keep it a roll
        # (capped at DECISION_MAX, above the vulnerable cap) — never a guaranteed turn.
        p = min(p + ATTITUDE_SNAP_DECISION_BONUS, ATTITUDE_SNAP_DECISION_MAX)
    return p


def _decision_flag_active(agenda, campaign: Campaign) -> bool:
    """Whether this agenda's decision-gated escalation is live (Layer 2).

    True only when the agenda names a `decision_flag` AND that CONTENT-defined flag is
    present and True in `Campaign.flags` (a recorded player choice set it). Reads only
    the engine-mutated `flags` dict — never fiction. Empty `decision_flag` => False, so
    an un-escalated agenda is byte-for-byte today's #142 behavior."""
    if agenda is None or not agenda.decision_flag:
        return False
    return bool(campaign.flags.get(agenda.decision_flag))


def _agenda_triggered(character: Character, campaign: Campaign, rng: random.Random | None = None) -> bool:
    """Whether the companion's agenda fires this beat.

    For ``attitude_below``: probabilistic (rising chance as attitude drops — see module
    docstring). A fresh ``random.Random()`` is used when no ``rng`` is passed; pass a
    seeded one for deterministic tests. A set `decision_flag` (Layer 2) spikes the chance.

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
            decision_flag_active=_decision_flag_active(agenda, campaign),
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


def _betrayal_warning(character: Character, campaign: Campaign) -> dict | None:
    """ADVISORY telegraph for an approaching betrayal (Layer 2) — never auto-acts.

    Returns a small advisory dict when the companion carries a LIVE (unfired)
    ``attitude_below`` agenda AND its attitude sits in the danger band
    [ATTITUDE_WARN_LOW, ATTITUDE_WARN_HIGH] — the relationship is fracturing but hasn't
    bottomed out, so the DM/Director can foreshadow the turn instead of springing it
    "from nowhere". Returns None outside the band, or when there is no live attitude_below
    agenda. Reads only the attitude gauge + the (engine-set) decision_flag — no fiction,
    no mutation. The `decision_flag_active` field lets the DM know a recorded choice has
    already spiked the odds (foreshadow harder)."""
    arc = character.arc
    if arc is None:
        return None
    agenda = arc.agenda
    if agenda is None or agenda.fired or agenda.trigger != "attitude_below":
        return None
    if agenda.value is None:
        return None
    av = character.attitude_value
    # Only warn while the bond is in the danger band AND has actually crossed below the
    # agenda's breaking point (an agenda whose threshold is even lower isn't live yet).
    if not (ATTITUDE_WARN_LOW <= av <= ATTITUDE_WARN_HIGH):
        return None
    if av >= agenda.value:
        return None
    return {
        "companion_id": character.id,
        "attitude_value": av,
        "threshold": agenda.value,
        "band": [ATTITUDE_WARN_LOW, ATTITUDE_WARN_HIGH],
        "decision_flag_active": _decision_flag_active(agenda, campaign),
        "note": (
            "approaching betrayal — this companion's bond is in the danger band; "
            "foreshadow the fracture before the agenda fires"
        ),
    }


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
    the moments that just became live, for the DM to dramatize. When a LIVE (unfired)
    ``attitude_below`` agenda's companion sits in the danger band, an advisory
    ``betrayal_warning`` is ALSO included (telegraph, never auto-acts; Layer 2). A
    character with no `arc` is a no-op (empty result)."""
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
    # Advisory telegraph: surface an approaching-betrayal warning while a live
    # attitude_below agenda sits in the danger band (Layer 2). Computed after the fire
    # check, so a betrayal that JUST fired this beat is the event, not a warning.
    warning = _betrayal_warning(character, campaign)
    if warning is not None:
        out["betrayal_warning"] = warning
    return out
