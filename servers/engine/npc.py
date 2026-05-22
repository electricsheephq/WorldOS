"""NPC social helpers: a five-step attitude track and check-driven shifts.

NPCs are ordinary Characters (kind="npc") carrying a free-text `attitude` and a
`memory` list. social_check maps the current attitude onto this track (an unknown
value is treated as 'indifferent') and shifts it one step.
"""

from __future__ import annotations

ATTITUDE_TRACK = ["hostile", "wary", "indifferent", "friendly", "helpful"]


def shift_attitude(current: str, steps: int) -> str:
    cur = (current or "indifferent").lower()
    idx = ATTITUDE_TRACK.index(cur) if cur in ATTITUDE_TRACK else 2  # unknown -> indifferent
    idx = max(0, min(len(ATTITUDE_TRACK) - 1, idx + steps))
    return ATTITUDE_TRACK[idx]
