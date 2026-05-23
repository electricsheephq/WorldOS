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

from models import Campaign, Consequence

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
    """Fire due thread-beats (thread_id set, trigger_day <= today, unfired), mark them,
    and reschedule each thread's NEXT beat ~_RECUR_DAYS out. Returns the fired beats for
    the DM to weave in. Mutates the campaign. Non-thread consequences are untouched
    (those are handled by `consequences.due`)."""
    fired: list[Consequence] = []
    for c in list(campaign.consequences):  # snapshot — we append while iterating
        if c.thread_id and not c.fired and c.trigger_day <= campaign.day:
            c.fired = True
            fired.append(c)
            base = c.note or c.text
            campaign.consequences.append(
                Consequence(
                    thread_id=c.thread_id,
                    trigger_day=campaign.day + _RECUR_DAYS,
                    text=base,
                    note=base,
                )
            )
            if len(fired) >= max(1, max_beats):
                break
    return fired


def pending_threads(campaign: Campaign) -> list[Consequence]:
    """Unfired thread-beats not yet due — for foresight / dashboards."""
    return [c for c in campaign.consequences if c.thread_id and not c.fired and c.trigger_day > campaign.day]
