"""NPC social helpers: a five-step attitude track and check-driven shifts.

NPCs are ordinary Characters (kind="npc") carrying a free-text `attitude` and a
`memory` list. social_check maps the current attitude onto this track (an unknown
value is treated as 'indifferent') and shifts it one step.
"""

from __future__ import annotations

ATTITUDE_TRACK = ["hostile", "wary", "indifferent", "friendly", "helpful"]

# Map common descriptive attitudes (authored adventures use free text like
# "guarded") onto the track, so a social check shifts them sensibly rather than
# collapsing every non-track value to "indifferent".
_SYNONYMS = {
    "angry": "hostile", "aggressive": "hostile", "furious": "hostile",
    "guarded": "wary", "suspicious": "wary", "anxious": "wary", "afraid": "wary",
    "nervous": "wary", "cautious": "wary",
    "neutral": "indifferent", "aloof": "indifferent",
    "grateful": "friendly", "warm": "friendly", "kind": "friendly", "trusting": "friendly",
    "devoted": "helpful", "loyal": "helpful",
}


def normalize(attitude: str) -> str:
    a = (attitude or "indifferent").lower()
    if a in ATTITUDE_TRACK:
        return a
    return _SYNONYMS.get(a, "indifferent")


def shift_attitude(current: str, steps: int) -> str:
    idx = ATTITUDE_TRACK.index(normalize(current))
    idx = max(0, min(len(ATTITUDE_TRACK) - 1, idx + steps))
    return ATTITUDE_TRACK[idx]
