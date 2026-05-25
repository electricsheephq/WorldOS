"""Background world-sim — the standing threads move on their own (R2).

A QA playtest flagged the gap: a seeded world's *standing threads* (a contested
dukedom, a cult recruiting, factions maneuvering) only advanced when the party chased
them. This module makes them advance in the BACKGROUND so the world feels alive even
when unwatched — built on the existing consequence clock (`Campaign.day`).

Pure module (no MCP, no I/O), mirrors `consequences.py`:
- ``seed_threads`` schedules one recurring "world beat" per standing thread (a
  ``Consequence`` with ``thread_id`` set), staggered over the first in-world days.
- ``tick`` fires the beats whose day has come, and **reschedules each thread's next
  beat** a few days out — so the threads keep ticking for the life of the campaign.
  The DM weaves a fired beat into play (a crier's notice, an overheard rumor, an
  off-screen development) and escalates it creatively; the engine just keeps a beat
  *available on the clock*.

Engine stays the sole writer: `tick`/`seed_threads` mutate the in-memory `Campaign`;
the MCP tool layer persists under `campaign_lock` + `save_campaign`.
"""

from __future__ import annotations

from models import BacklogItem, Campaign, Consequence

_RECUR_DAYS = 4  # how far out a thread's next beat is rescheduled after one fires


def seed_threads(campaign: Campaign, threads: list[str], start_day: int = 3, stagger: int = 2) -> int:
    """Schedule one recurring world-beat per standing thread (mutates). Returns count.
    Beats stagger over days so they don't all land at once."""
    n = 0
    for i, raw in enumerate(threads or []):
        text = str(raw).strip()
        if not text:
            continue
        campaign.consequences.append(
            Consequence(
                thread_id=f"thread-{i + 1}",
                trigger_day=campaign.day + start_day + i * stagger,
                text=text,
                note=text,  # the thread's base text, reused when rescheduling
            )
        )
        n += 1
    return n


def tick(campaign: Campaign, max_beats: int = 2) -> list[Consequence]:
    """Fire due thread-beats (thread_id set, trigger_day <= today) and RE-ARM each in
    place ~_RECUR_DAYS out. A standing thread is a PERPETUAL TIMER, not a one-shot, so
    the same record is just pushed forward rather than marked fired + replaced by a new
    one — that keeps exactly one record per thread for the life of the campaign (no
    unbounded growth of dead `fired` consequences) while the thread keeps ticking.
    Returns the beats that fired this call for the DM to weave in. Mutates the campaign.
    Non-thread consequences are untouched (those are handled by `consequences.due`)."""
    fired: list[Consequence] = []
    for c in campaign.consequences:  # re-arm in place -> no append, no snapshot needed
        if c.thread_id and not c.fired and c.trigger_day <= campaign.day:
            fired.append(c)
            c.trigger_day = campaign.day + _RECUR_DAYS  # the timer rolls forward
            if len(fired) >= max(1, max_beats):
                break
    return fired


def pending_threads(campaign: Campaign) -> list[Consequence]:
    """Unfired thread-beats not yet due — for foresight / dashboards."""
    return [c for c in campaign.consequences if c.thread_id and not c.fired and c.trigger_day > campaign.day]


# --- The PROACTIVE living-world backlog (P0/P1) -----------------------------------------------
# The world's own to-do, advanced MECHANICALLY for free on the in-world day clock so the campaign
# moves off-screen when in-fiction time passes. Kept STRICTLY separate from Consequence/thread
# beats above (its own typed block on Campaign — `campaign_backlog` — never consumed by
# consequences.due). Mirrors the tick/pending shape of the thread helpers; pure (no I/O).


def _apply_backlog_effect(campaign: Campaign, item: BacklogItem) -> str:
    """Apply a DETERMINISTIC backlog item's structured `effect` to the campaign (mutates) and
    return the one-line `summary` of what changed, for the DM to weave. A number, flag, or stub
    only — NEVER prose generation (that's the later DM/agent for needs_llm items). Setting-
    agnostic: every key/value comes from CONTENT (the seeded `effect` dict), never engine code.
    Unknown/empty effect keys fall through to a generic marker so a malformed effect degrades to
    a no-op development rather than raising — the engine stays the unbreakable, always-on tick."""
    eff = item.effect or {}

    # A campaign flag the DM/engine gates events on (e.g. {"flag": "concord_split"}).
    flag = str(eff.get("flag") or "").strip()
    if flag:
        campaign.flags[flag] = True

    # A faction reputation drift (e.g. {"faction_id": "...", "reputation_delta": "-5"}). Clamped
    # to the model's -100..100 band. A faction that no longer exists is skipped (degrade).
    fac_id = str(eff.get("faction_id") or "").strip()
    delta_raw = eff.get("reputation_delta")
    if fac_id and delta_raw is not None and fac_id in campaign.factions:
        try:
            delta = int(delta_raw)
        except (TypeError, ValueError):
            delta = 0
        fac = campaign.factions[fac_id]
        fac.reputation = max(-100, min(100, fac.reputation + delta))

    # A faction-control shift recorded as a deterministic flag (no RegionControl model in this
    # engine — epic #60's StrategicState is subsumed, not rebuilt). {"controller_id":"fac-x",
    # "location_id":"loc-y"} -> a `control:loc-y=fac-x` flag the DM reads + dramatizes.
    controller_id = str(eff.get("controller_id") or "").strip()
    loc_id = str(eff.get("location_id") or "").strip()
    if controller_id and loc_id:
        campaign.flags[f"control:{loc_id}={controller_id}"] = True

    # A scheduled NPC arrival STUBBED as a flag (F2 — the structure now, the voice/motive later
    # from the DM/agent). The arrival's existence is a deterministic fact; its prose is not.
    npc_name = str(eff.get("npc_name") or "").strip()
    if npc_name:
        where = f" at {loc_id}" if loc_id else ""
        campaign.flags[f"arrival:{npc_name}{where}"] = True

    # The DM-facing one-liner: prefer an authored summary, else a terse template of what moved.
    if item.summary.strip():
        return item.summary.strip()
    if item.title.strip():
        return item.title.strip()
    return f"An off-screen development ({item.kind}) advanced the world."


def tick_backlog(campaign: Campaign, max_events: int = 2) -> list[BacklogItem]:
    """Advance the proactive campaign backlog by the in-world days elapsed since
    `campaign_backlog.last_tick_day` (mutates). IDEMPOTENT BY ELAPSED DAYS — exactly the #60
    StrategicState contract: ``elapsed = campaign.day - last_tick_day``; if ``elapsed <= 0`` it
    is a pure no-op, so repeated advance_time/world_tick/downtime/travel_to on the SAME day never
    double-advance even though all five tools call this. (Never a call counter.)

    For each `pending` item whose `trigger_day <= campaign.day`, FIRE it — capped at `max_events`
    per call so a long jump doesn't dump the whole board at once:
      * DETERMINISTIC (`needs_llm=False`): apply its structured `effect` to the campaign (a flag,
        a faction-reputation/control shift, an NPC-arrival stub) via `_apply_backlog_effect`, set
        its `summary`, and resolve it (`status -> "resolved"`). A NUMBER/FLAG/STUB only — the
        engine never invents prose.
      * CREATIVE (`needs_llm=True`): only ENQUEUE it (`status -> "fired"`) for the later DM digest
        (P2) / world-agent (P3); apply NO effect, generate NO prose here.
    A recurring item (`cadence_days > 0`) RE-ARMS in place (status back to "pending",
    `trigger_day = campaign.day + cadence_days`) — like worldsim.tick re-arming a thread, so the
    record count stays put (no per-tick append). A one-shot (`cadence_days == 0`) stays
    fired/resolved.

    Sets `last_tick_day = campaign.day` after advancing. Returns the items that FIRED this call
    (for the DM to weave / the agent to author). Touches ONLY `campaign.campaign_backlog` (+ the
    flags/factions a deterministic effect names); NEVER `c.consequences` — it is never consumed
    by consequences.due (a strict sibling of the narrative-beat layer)."""
    bl = campaign.campaign_backlog
    elapsed = campaign.day - bl.last_tick_day
    if elapsed <= 0:
        return []  # idempotent: same day (or clock not yet rolled a day) -> no-op
    fired: list[BacklogItem] = []
    cap = max(1, max_events)
    # Deterministic order (by trigger_day then id) so a capped tick fires the most-overdue first
    # and is reproducible regardless of dict insertion order.
    for item in sorted(bl.items.values(), key=lambda i: (i.trigger_day, i.id)):
        if item.status != "pending" or item.trigger_day > campaign.day:
            continue
        if item.needs_llm:
            item.status = "fired"  # enqueue only — voicing/authoring is P2/P3, not the engine
        else:
            item.summary = _apply_backlog_effect(campaign, item)
            item.status = "resolved"
        if item.cadence_days > 0:  # a recurring development re-arms in place (no append/growth)
            item.trigger_day = campaign.day + item.cadence_days
            item.status = "pending"
        fired.append(item)
        if len(fired) >= cap:
            break
    bl.last_tick_day = campaign.day
    return fired


def pending_backlog(campaign: Campaign) -> list[BacklogItem]:
    """Read-only foresight: backlog items still `pending` and not yet due, soonest-first — for
    dashboards / the later DM digest. (`fired` items awaiting the world-agent and `resolved`
    ones are excluded — those are no longer owed work on the clock.)"""
    bl = campaign.campaign_backlog
    return sorted(
        (i for i in bl.items.values() if i.status == "pending" and i.trigger_day > campaign.day),
        key=lambda i: (i.trigger_day, i.id),
    )
