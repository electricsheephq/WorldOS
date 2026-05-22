"""Time-deferred consequences — the engine's "living world" clock hook (P2.6).

Pure module (no MCP, no I/O). A Consequence is something scheduled to come due on
a future in-world day (``Campaign.day``): a ritual finishing, a spared villain
returning, a siege arriving, a debt called in. ``schedule`` adds one relative to
today; ``due`` returns (and marks fired) the ones that have arrived. This is the
cheapest mechanic that turns a series of disconnected dungeons into a campaign
whose world keeps moving while the party is busy elsewhere.
"""

from __future__ import annotations

from models import Campaign, Consequence


def schedule(campaign: Campaign, in_days: int, text: str, note: str = "") -> Consequence:
    """Schedule a consequence to fire ``in_days`` from the current day (mutates)."""
    conseq = Consequence(
        trigger_day=campaign.day + max(0, int(in_days)), text=text, note=note
    )
    campaign.consequences.append(conseq)
    return conseq


def due(campaign: Campaign) -> list[Consequence]:
    """Return the unfired consequences whose trigger day has arrived (<= the
    current day), marking each fired. Mutates the campaign."""
    out: list[Consequence] = []
    for c in campaign.consequences:
        if not c.fired and c.trigger_day <= campaign.day:
            c.fired = True
            out.append(c)
    return out


def pending(campaign: Campaign) -> list[Consequence]:
    """Unfired consequences not yet due — for DM foresight / a dashboard."""
    return [c for c in campaign.consequences if not c.fired and c.trigger_day > campaign.day]
