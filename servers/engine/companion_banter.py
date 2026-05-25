"""Deterministic camp-beat and companion-banter scheduler.

Pure module: no MCP, no campaign I/O, no persistence. The engine server owns the
load/lock/save path and records selected beats explicitly.
"""

from __future__ import annotations

import re
from itertools import combinations

from models import CampBeatCandidate, Campaign, Character


def pair_key(*companion_ids: str) -> str:
    """Stable pair key used for cooldowns and persisted records."""
    return "|".join(sorted(str(cid) for cid in companion_ids if str(cid).strip()))


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return value.strip("-")[:48] or "general"


def _living_companions(campaign: Campaign) -> list[Character]:
    companions = [
        campaign.characters[pid]
        for pid in campaign.party
        if pid in campaign.characters
        and campaign.characters[pid].kind == "companion"
        and not campaign.characters[pid].dead
        and campaign.characters[pid].current_hp > 0
    ]
    return sorted(companions, key=lambda ch: ch.id)


def _last_recorded_day(campaign: Campaign) -> dict[str, int]:
    latest: dict[str, int] = {}
    for record in campaign.camp_beats.records:
        keys = {record.id, record.cooldown_key}
        if record.pair_key:
            keys.add(f"pair:{record.pair_key}")
        for key in keys:
            if key:
                latest[key] = max(latest.get(key, -1), record.day)
    return latest


def _is_on_cooldown(campaign: Campaign, candidate: CampBeatCandidate, latest: dict[str, int]) -> bool:
    cooldown_days = (
        campaign.camp_beats.pair_cooldown_days
        if candidate.kind == "pair_banter"
        else campaign.camp_beats.solo_cooldown_days
    )
    keys = [candidate.beat_id, candidate.cooldown_key]
    if candidate.pair_key:
        keys.append(f"pair:{candidate.pair_key}")
    for key in keys:
        if key in latest and campaign.day - latest[key] < cooldown_days:
            return True
    return False


def _dossier_hooks(companion: Character) -> tuple[str, list[str], int]:
    dossier = companion.companion_dossier
    if dossier is None:
        return ("a quiet check-in about recent events and the party's direction", [], 50)
    if dossier.camp_prompts:
        prompt = dossier.camp_prompts[0]
        tags = list(dossier.banter_tags[:3])
        return (prompt, tags, 90)
    tags = list(dossier.banter_tags[:3] or dossier.values[:3] or dossier.wants[:3])
    if tags:
        return (f"a camp moment around {', '.join(tags)}", tags, 70)
    if dossier.wound:
        return (f"a guarded moment touching the wound: {dossier.wound}", ["wound"], 65)
    return ("a quiet check-in about recent events and the party's direction", [], 50)


def _solo_candidate(companion: Character) -> CampBeatCandidate:
    hook, tags, priority = _dossier_hooks(companion)
    anchor = _slug("|".join(tags) or hook)
    beat_id = f"camp:solo:{companion.id}:{anchor}"
    return CampBeatCandidate(
        beat_id=beat_id,
        kind="solo",
        priority=priority,
        companion_ids=[companion.id],
        prompt=(
            f"Frame a camp beat for {companion.name}: {hook}. "
            "Give the companion a question, concern, or memory to bring forward; let the table voice the words."
        ),
        tags=tags,
        cooldown_key=f"solo:{companion.id}:{anchor}",
    )


def _shared_pair_tags(a: Character, b: Character) -> list[str]:
    ad = a.companion_dossier
    bd = b.companion_dossier
    atags = set(ad.banter_tags if ad is not None else [])
    btags = set(bd.banter_tags if bd is not None else [])
    shared = sorted(atags & btags)
    if shared:
        return shared[:3]
    fallback = sorted((atags | btags))[:3]
    return fallback


def _pair_candidate(a: Character, b: Character) -> CampBeatCandidate:
    pkey = pair_key(a.id, b.id)
    tags = _shared_pair_tags(a, b)
    anchor = _slug("|".join(tags) or "contrast")
    focus = f"shared hooks: {', '.join(tags)}" if tags else "their contrasting reads on the last leg of travel"
    return CampBeatCandidate(
        beat_id=f"camp:pair:{pkey}:{anchor}",
        kind="pair_banter",
        priority=40 + len(tags),
        companion_ids=sorted([a.id, b.id]),
        prompt=(
            f"Frame a brief camp banter exchange for {a.name} and {b.name} around {focus}. "
            "Use it to reveal stance, tension, or warmth without resolving the scene for them."
        ),
        tags=tags,
        cooldown_key=f"pair:{pkey}:{anchor}",
        pair_key=pkey,
    )


def schedule_camp_beats(campaign: Campaign, max_beats: int = 6) -> list[CampBeatCandidate]:
    """Return deterministic, currently-available camp beat frames.

    The scheduler is a pure read of Campaign state. It excludes PCs and dead/downed
    companions, requires two living companions for pair banter, and filters by persisted
    camp-beat history without recording anything itself.
    """
    companions = _living_companions(campaign)
    latest = _last_recorded_day(campaign)
    candidates: list[CampBeatCandidate] = [_solo_candidate(comp) for comp in companions]
    if len(companions) >= 2:
        candidates.extend(_pair_candidate(a, b) for a, b in combinations(companions, 2))
    available = [candidate for candidate in candidates if not _is_on_cooldown(campaign, candidate, latest)]
    available.sort(key=lambda beat: (-beat.priority, beat.kind, beat.cooldown_key))
    return available[: max(0, int(max_beats))]
