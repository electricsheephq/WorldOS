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
    """Every LIVING companion the camp scene gathers — sorted by id for determinism.

    F06-5 (audit 2026-06-11): includes DE-FACTO companions (kind=='companion' but absent
    from `c.party` — e.g. a canon companion loaded with add_to_party=False and never
    formally recruited). Camp was the one seam that gated on `c.party` while the relocate
    sweep (#353) + XP split (#739) include any kind=='companion'; a de-facto companion
    walks WITH the party and so must breathe at camp WITH them. Iterates the full roster,
    not just `c.party`, matching `_party_xp_recipients`'s de-facto rule."""
    companions = [
        ch
        for ch in campaign.characters.values()
        if ch.kind == "companion"
        and not ch.dead
        and ch.current_hp > 0
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


def _solo_rotation(campaign: Campaign, companion: Character) -> int:
    """How many SOLO camp beats this companion has already had RECORDED — the rotation
    index into the dossier's `camp_prompts` (F06-5 leg b). A pure read of persisted
    history: `camp_scene` never records, so this advances only when `record_camp_beat`
    actually appends a played beat. Zero for a companion who has never had a camp moment,
    so the first visit always voices `camp_prompts[0]` (today's behavior)."""
    return sum(
        1
        for record in campaign.camp_beats.records
        if record.kind == "solo" and companion.id in record.companion_ids
    )


def _dossier_hooks(companion: Character, rotation: int = 0) -> tuple[str, list[str], int]:
    """The solo camp hook for a companion: a (prompt, tags, priority) triple.

    F06-5 leg (b) (audit 2026-06-11): when the dossier carries authored `camp_prompts`,
    ROTATE through them by `rotation` (the count of this companion's recorded solo beats)
    instead of hard-indexing `camp_prompts[0]` forever — so prompts 1..N are live content,
    not dead weight, and each fresh camp visit voices a new hook. Wraps modulo the prompt
    count so a long campaign keeps cycling. The anchor SLUG folds in the rotation when there
    are no tags, so the beat id + cooldown_key change per prompt even for a tagless dossier
    (otherwise the cooldown would re-suppress the rotated beat). `rotation=0` (the default)
    reproduces today's first-visit behavior byte-for-byte."""
    dossier = companion.companion_dossier
    if dossier is None:
        return ("a quiet check-in about recent events and the party's direction", [], 50)
    if dossier.camp_prompts:
        idx = rotation % len(dossier.camp_prompts)
        prompt = dossier.camp_prompts[idx]
        tags = list(dossier.banter_tags[:3])
        return (prompt, tags, 90)
    tags = list(dossier.banter_tags[:3] or dossier.values[:3] or dossier.wants[:3])
    if tags:
        return (f"a camp moment around {', '.join(tags)}", tags, 70)
    if dossier.wound:
        return (f"a guarded moment touching the wound: {dossier.wound}", ["wound"], 65)
    return ("a quiet check-in about recent events and the party's direction", [], 50)


def _solo_candidate(companion: Character, campaign: Campaign | None = None) -> CampBeatCandidate:
    """A deterministic solo camp beat for a companion. When `campaign` is passed the prompt
    ROTATES through the dossier's authored `camp_prompts` by recorded-solo count (F06-5 b);
    omit it (legacy callers) and the beat is the first-visit beat (rotation 0)."""
    rotation = _solo_rotation(campaign, companion) if campaign is not None else 0
    hook, tags, priority = _dossier_hooks(companion, rotation=rotation)
    base = _slug("|".join(tags) or hook)
    # The anchor distinguishes one ROTATED prompt from the next so a fresh prompt is NOT
    # re-suppressed by the prior prompt's cooldown. We only ADD the rotation suffix when the
    # dossier carries MULTIPLE authored camp_prompts (the path `_dossier_hooks` actually
    # rotates) AND the wheel has turned — otherwise (a tagless/default/single-prompt dossier
    # where the hook is constant) the anchor stays exactly as it was, so existing cooldown +
    # compaction behavior is byte-for-byte unchanged (back-compat).
    dossier = companion.companion_dossier
    rotating = dossier is not None and len(dossier.camp_prompts) > 1
    anchor = f"{base}-r{rotation}" if (rotating and rotation > 0) else base
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
    candidates: list[CampBeatCandidate] = [_solo_candidate(comp, campaign) for comp in companions]
    if len(companions) >= 2:
        candidates.extend(_pair_candidate(a, b) for a, b in combinations(companions, 2))
    available = [candidate for candidate in candidates if not _is_on_cooldown(campaign, candidate, latest)]
    available.sort(key=lambda beat: (-beat.priority, beat.kind, beat.cooldown_key))
    return available[: max(0, int(max_beats))]
