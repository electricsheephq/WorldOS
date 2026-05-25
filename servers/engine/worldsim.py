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


def _apply_structured_effect(campaign: Campaign, effect: dict, *, fallback: str) -> str:
    """Apply a deterministic strategic/backlog effect (mutates) and return a DM-facing line.

    The payload is intentionally tiny and setting-agnostic: flags, faction reputation,
    faction control markers, and NPC-arrival stubs. Unknown keys are harmless no-ops."""
    eff = effect or {}

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

    return fallback.strip() or "A structured world-state effect resolved."


def _apply_backlog_effect(campaign: Campaign, item: BacklogItem) -> str:
    """Apply a DETERMINISTIC backlog item's structured `effect` to the campaign (mutates) and
    return the one-line `summary` of what changed, for the DM to weave. A number, flag, or stub
    only — NEVER prose generation (that's the later DM/agent for needs_llm items). Setting-
    agnostic: every key/value comes from CONTENT (the seeded `effect` dict), never engine code.
    Unknown/empty effect keys fall through to a generic marker so a malformed effect degrades to
    a no-op development rather than raising — the engine stays the unbreakable, always-on tick."""
    # The DM-facing one-liner: prefer an authored summary, else a terse template of what moved.
    if item.summary.strip():
        fallback = item.summary.strip()
    elif item.title.strip():
        fallback = item.title.strip()
    else:
        fallback = f"An off-screen development ({item.kind}) advanced the world."
    return _apply_structured_effect(campaign, item.effect, fallback=fallback)


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
    capped = False
    # The highest trigger_day among items we ACTUALLY fired (captured BEFORE a recurring item
    # re-arms its trigger_day forward). When the cap stops us with due items still pending, we
    # only advance last_tick_day to here — not all the way to campaign.day — so a subsequent tick
    # on the same/next day still drains the remainder. Already-fired items are status-guarded
    # (status != "pending"), so they are never re-fired.
    last_fired_trigger_day = bl.last_tick_day
    # Deterministic order (by trigger_day then id) so a capped tick fires the most-overdue first
    # and is reproducible regardless of dict insertion order.
    for item in sorted(bl.items.values(), key=lambda i: (i.trigger_day, i.id)):
        if item.status != "pending" or item.trigger_day > campaign.day:
            continue
        last_fired_trigger_day = max(last_fired_trigger_day, item.trigger_day)  # pre-re-arm value
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
            capped = True
            break
    # Not capped -> we drained everything due through today, so advance to campaign.day (and never
    # re-enter on the same day). Capped with pending work left -> advance only to the highest day
    # we fired, leaving room for the next tick to fire the strays without re-firing the done ones.
    bl.last_tick_day = last_fired_trigger_day if capped else campaign.day
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


# --- Typed strategic board advancement (#75) --------------------------------------------------


def _cadence_ticks(last_day: int, current_day: int, cadence_days: int) -> int:
    """How many cadence boundaries were crossed between two campaign days."""
    cadence = max(0, int(cadence_days))
    if cadence <= 0 or current_day <= last_day:
        return 0
    # Day 1 with cadence 3 ticks after three elapsed days: on day 4, then 7, 10, ...
    return max(0, (current_day - 1) // cadence - (last_day - 1) // cadence)


def tick_strategic(campaign: Campaign) -> list[dict]:
    """Advance typed strategic clocks and active downtime projects for elapsed days.

    This mutates only ``campaign.strategic_state`` and deterministic campaign fields named by
    project effects. The guard is day-based: if ``campaign.day <= last_tick_day`` it is a no-op,
    so repeated ``world_tick`` calls on the same day never double-progress or spam events.
    Narrative ``Consequence`` records are deliberately untouched."""
    st = campaign.strategic_state
    last_day = st.last_tick_day
    elapsed = campaign.day - last_day
    if elapsed <= 0:
        return []

    events: list[dict] = []

    for clock in sorted(st.clocks.values(), key=lambda x: x.id):
        if clock.progress >= clock.target:
            continue
        delta = _cadence_ticks(last_day, campaign.day, clock.tick_every_days)
        if delta <= 0:
            continue
        before = clock.progress
        clock.progress = min(clock.target, clock.progress + delta)
        due = before < clock.target and clock.progress >= clock.target
        events.append(
            {
                "type": "clock_due" if due else "clock_advanced",
                "id": clock.id,
                "title": clock.title,
                "kind": clock.kind,
                "scope": clock.scope,
                "progress": clock.progress,
                "target": clock.target,
                "delta": clock.progress - before,
                "due": due,
                "line": clock.note or clock.title,
            }
        )

    for project in sorted(st.projects.values(), key=lambda x: x.id):
        if project.status != "active":
            continue
        before = project.progress_days
        project.progress_days = min(project.duration_days, project.progress_days + elapsed)
        if project.progress_days <= before:
            continue
        complete = before < project.duration_days and project.progress_days >= project.duration_days
        event_type = "project_complete" if complete else "project_advanced"
        line = project.note or project.title
        if complete:
            project.status = "complete"
            line = _apply_structured_effect(campaign, project.effect, fallback=line)
        events.append(
            {
                "type": event_type,
                "id": project.id,
                "title": project.title,
                "kind": project.kind,
                "progress_days": project.progress_days,
                "duration_days": project.duration_days,
                "delta": project.progress_days - before,
                "complete": complete,
                "line": line,
            }
        )

    st.last_tick_day = campaign.day
    return events
