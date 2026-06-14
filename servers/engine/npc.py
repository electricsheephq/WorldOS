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
    # "unfriendly" is the 3.5e/PF five-step diplomacy track word a DM reaches for; the
    # engine's own track mirrors it as "wary" (F10-6c). Without this entry it fell through
    # to "indifferent" — a whole band too friendly for what the DM actually wrote.
    "guarded": "wary", "suspicious": "wary", "anxious": "wary", "afraid": "wary",
    "nervous": "wary", "cautious": "wary", "unfriendly": "wary",
    "neutral": "indifferent", "aloof": "indifferent",
    "grateful": "friendly", "warm": "friendly", "kind": "friendly", "trusting": "friendly",
    "devoted": "helpful", "loyal": "helpful",
}


def normalize(attitude: str) -> str:
    a = (attitude or "indifferent").lower()
    if a in ATTITUDE_TRACK:
        return a
    return _SYNONYMS.get(a, "indifferent")


# The numeric per-NPC relationship (attitude_value, -100..+100) projected onto the
# five-step free-text track (F10-6a). This is the one bridge that reconciles the two
# attitude tracks: the band the dashboard / parley can DERIVE from the number when no
# label is set. Symmetric around 0 so a "friendly" +40 mirrors a "wary" -40. Cutoffs
# (inclusive on the low side):
#   <= -60 hostile | -59..-20 wary | -19..+19 indifferent | +20..+59 friendly | >= +60 helpful
def band_for_value(value: int) -> str:
    v = max(-100, min(100, int(value)))
    if v <= -60:
        return "hostile"
    if v <= -20:
        return "wary"
    if v < 20:
        return "indifferent"
    if v < 60:
        return "friendly"
    return "helpful"


def shift_attitude(current: str, steps: int) -> str:
    idx = ATTITUDE_TRACK.index(normalize(current))
    idx = max(0, min(len(ATTITUDE_TRACK) - 1, idx + steps))
    return ATTITUDE_TRACK[idx]
