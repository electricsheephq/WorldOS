"""Campaign Director — structural scene-debt detection (issue #72).

ADVISORY ONLY: this module detects structural debts from engine STATE and returns
them as ``SceneDebt`` objects. It NEVER acts on debts, mutates fiction, or makes
narrative-quality judgments. The DM reads the advisory and CHOOSES what to honour.
Resolution is always EXPLICIT via ``resolve_scene_debt``, never automatic.

PURE (no I/O): the only input is a ``Campaign`` snapshot in memory. Idempotent
and deterministic — re-detecting on the same snapshot yields the same result.
"""

from __future__ import annotations

import hashlib

from models import Campaign, SceneDebt


def _debt_id(kind: str, subject: str) -> str:
    """Deterministic debt id from kind + subject — stable across re-detections
    of the same campaign snapshot. This makes resolve_scene_debt reliable: the
    id returned by get_scene_debts matches the id a subsequent detect() produces
    for the same structural fact."""
    h = hashlib.sha1(f"{kind}:{subject}".encode()).hexdigest()[:12]
    return f"debt_{h}"

# ── Thresholds (structural; tune via QA) ──────────────────────────────────────

# A quest with no status-change Decision in this many campaign days is "stalled".
# Quests have no own `day` field, so we proxy: a Decision whose summary/rationale
# references the quest id is the resolution signal. If no such Decision exists we
# compare quest id creation-order to the campaign day (conservative: flag only
# when the campaign is meaningfully advanced past the quest's arrival). Because
# Quest has no `day` field we use a simpler proxy: an active quest whose giver is
# known but there's been no matching Decision in the last N days of campaign time.
QUEST_STALL_DAYS: int = 5  # in-world days with no decision-callback for the quest

# A thread is "overdue for a world beat" when its next scheduled beat is already
# past, meaning it should have fired but hasn't yet (trigger_day < campaign.day
# and not fired). Re-armed threads (worldsim) roll their trigger_day forward, so
# only threads whose timer hasn't fired are flagged.
THREAD_OVERDUE_DAYS: int = 0  # trigger_day strictly past (< campaign.day)

# NPCs at the current location are "silent" if they were met but have no memory
# entries yet — proxy for "introduced but hasn't spoken". We flag only NPCs whose
# `met` flag is True (i.e. the DM introduced them) AND who have no memory entries.
# Companions are excluded (they have their own banter system).


# ── Detection helpers ─────────────────────────────────────────────────────────


def _hook_has_quest(c: Campaign, hook_id: str) -> bool:
    """Return True if any tracked Quest's description/title contains the hook id
    OR if the hook's title matches any tracked quest's title (case-insensitive).
    Also True when any Quest's giver_id matches the hook's giver_id (same NPC
    quest origin = likely the same storyline)."""
    h = next((x for x in c.quest_hooks if x.id == hook_id), None)
    if h is None:
        return True  # no hook → not our concern
    for q in c.quests.values():
        if hook_id in (q.description or "") or hook_id in (q.title or ""):
            return True
        if h.title and h.title.lower() in (q.title or "").lower():
            return True
        if h.giver_id and h.giver_id == q.giver_id:
            return True
    return False


def _hook_player_engaged(c: Campaign, hook_id: str) -> bool:
    """A hook is 'engaged' when a Decision references it OR when the hook status
    has been advanced to 'active' (the DM manually marked it active, meaning the
    party bit on it). Both are structural signals in the snapshot."""
    h = next((x for x in c.quest_hooks if x.id == hook_id), None)
    if h is None:
        return False
    if h.status == "active":
        return True
    # A decision whose summary/rationale contains the hook id or the hook title
    hook_title_lower = (h.title or "").lower()
    for d in c.decisions:
        text = (d.summary + " " + d.rationale + " " + d.chosen).lower()
        if hook_id in text:
            return True
        if hook_title_lower and hook_title_lower in text:
            return True
    return False


def _quest_has_decision_callback(c: Campaign, quest_id: str, quest_title: str, within_days: int) -> bool:
    """Return True if there's a Decision in the last `within_days` of campaign time
    referencing this quest (by id or title). A Decision's `day` field tracks when
    it was recorded."""
    min_day = c.day - within_days
    title_lower = quest_title.lower()
    for d in c.decisions:
        if d.day < min_day:
            continue
        text = (d.summary + " " + d.rationale + " " + d.chosen + " " + " ".join(d.options)).lower()
        if quest_id in text or (title_lower and title_lower in text):
            return True
    return False


# ── 6 structural detectors ───────────────────────────────────────────────────


def _detect_hook_untracked(c: Campaign) -> list[SceneDebt]:
    """hook_untracked — a quest_hook the player engaged with no matching tracked Quest.
    THE add_quest fix: when a hook is 'active' or referenced by a Decision but no
    Quest was created for it, the DM owes a quest record."""
    debts: list[SceneDebt] = []
    for h in c.quest_hooks:
        if h.status == "resolved":
            continue  # already resolved; not a debt
        if not _hook_player_engaged(c, h.id):
            continue  # player hasn't bitten; not yet a debt
        if _hook_has_quest(c, h.id):
            continue  # already tracked
        debts.append(
            SceneDebt(
                id=_debt_id("hook_untracked", h.id),
                kind="hook_untracked",
                subject=h.id,
                detail=f"Hook '{h.title or h.id}' is active but has no tracked Quest — call add_quest.",
                severity="high",
                evidence={"hook_id": h.id, "hook_title": h.title, "hook_status": h.status},
            )
        )
    return debts


def _detect_quest_stalled(c: Campaign) -> list[SceneDebt]:
    """quest_stalled — an active Quest with no decision-callback in >= QUEST_STALL_DAYS.
    Quest has no own 'day' field; we proxy via Decisions that reference it.
    A brand-new campaign (day <= threshold) generates no debts."""
    if c.day <= QUEST_STALL_DAYS:
        return []
    debts: list[SceneDebt] = []
    for q in c.quests.values():
        if q.status != "active":
            continue
        if _quest_has_decision_callback(c, q.id, q.title, QUEST_STALL_DAYS):
            continue
        debts.append(
            SceneDebt(
                id=_debt_id("quest_stalled", q.id),
                kind="quest_stalled",
                subject=q.id,
                detail=(
                    f"Quest '{q.title}' is active but has no story callback in the last "
                    f"{QUEST_STALL_DAYS} campaign days — needs an advancement beat."
                ),
                severity="med",
                evidence={"quest_id": q.id, "quest_title": q.title, "campaign_day": c.day},
            )
        )
    return debts


def _detect_choice_without_outcome(c: Campaign) -> list[SceneDebt]:
    """choice_without_outcome — a Decision that was offered/recorded but `chosen` is
    still empty ('pending'), meaning the party was presented a choice that was never
    resolved in the snapshot. Detection: Decision.chosen == "" and Decision.options
    is non-empty (a real offered choice, not a bare fact record)."""
    debts: list[SceneDebt] = []
    for d in c.decisions:
        if d.options and not d.chosen.strip():
            debts.append(
                SceneDebt(
                    id=_debt_id("choice_without_outcome", d.id),
                    kind="choice_without_outcome",
                    subject=d.id,
                    detail=(
                        f"Decision '{d.summary}' was offered (options: {d.options!r}) "
                        f"but 'chosen' is empty — the party's choice was never recorded."
                    ),
                    severity="high",
                    evidence={"decision_id": d.id, "summary": d.summary, "options": d.options},
                )
            )
    return debts


def _detect_due_consequence(c: Campaign) -> list[SceneDebt]:
    """due_consequence — an authored Consequence past its trigger_day, not yet fired.
    These are non-thread consequences (thread_id == "") that the DM hasn't surfaced.
    Detection uses the same criteria as consequences.due() but read-only."""
    debts: list[SceneDebt] = []
    for con in c.consequences:
        if con.thread_id:
            continue  # worldsim thread-beats; handled by thread_pressure
        if con.fired:
            continue
        if con.trigger_day <= c.day:
            overdue_days = c.day - con.trigger_day
            debts.append(
                SceneDebt(
                    id=_debt_id("due_consequence", con.id),
                    kind="due_consequence",
                    subject=con.id,
                    detail=(
                        f"Consequence '{con.text[:80]}' was due on day {con.trigger_day} "
                        f"({overdue_days} day(s) ago) — call check_consequences to surface it."
                    ),
                    severity="high" if overdue_days >= 2 else "med",
                    evidence={
                        "consequence_id": con.id,
                        "trigger_day": con.trigger_day,
                        "campaign_day": c.day,
                        "overdue_days": overdue_days,
                        "note": con.note,
                    },
                )
            )
    return debts


def _detect_thread_pressure(c: Campaign) -> list[SceneDebt]:
    """thread_pressure — a worldsim standing thread whose next beat is already past
    (trigger_day <= campaign.day) and hasn't re-armed (i.e. it hasn't been ticked).
    worldsim.tick re-arms in place, so an un-ticked overdue thread means world_tick
    hasn't been called yet this 'day'. Detection: thread_id non-empty, not fired,
    trigger_day <= campaign.day."""
    debts: list[SceneDebt] = []
    for con in c.consequences:
        if not con.thread_id:
            continue  # authored consequences; handled by due_consequence
        if con.fired:
            continue
        if con.trigger_day <= c.day:
            debts.append(
                SceneDebt(
                    id=_debt_id("thread_pressure", con.id),
                    kind="thread_pressure",
                    subject=con.id,
                    detail=(
                        f"Standing thread '{con.thread_id}' beat is overdue "
                        f"(due day {con.trigger_day}, now day {c.day}) — call world_tick."
                    ),
                    severity="med",
                    evidence={
                        "consequence_id": con.id,
                        "thread_id": con.thread_id,
                        "trigger_day": con.trigger_day,
                        "campaign_day": c.day,
                        "text": con.text[:80],
                    },
                )
            )
    return debts


def _detect_npc_introduced_silent(c: Campaign) -> list[SceneDebt]:
    """npc_introduced_silent — an NPC/monster at the current location, marked as `met`
    (introduced), with no memory entries. `met=True` means the engine or DM introduced
    them at a first-contact tool (social_check, recruit_companion, load_canon_character),
    but no facts have been recorded about them yet — they've appeared but haven't spoken.
    Companions are excluded (they have the banter system and are expected to be quieter
    at first). Only checks characters at the CURRENT location to keep it scoped."""
    if not c.current_location_id:
        return []
    debts: list[SceneDebt] = []
    for ch in c.characters.values():
        if ch.kind not in ("npc", "monster"):
            continue
        if ch.location_id != c.current_location_id:
            continue
        if not ch.met:
            continue
        if ch.memory:
            continue  # has spoken / facts recorded
        debts.append(
            SceneDebt(
                id=_debt_id("npc_introduced_silent", ch.id),
                kind="npc_introduced_silent",
                subject=ch.id,
                detail=(
                    f"NPC '{ch.name}' at current location has been introduced (met=True) "
                    f"but has no memory entries — they haven't spoken yet."
                ),
                severity="low",
                evidence={
                    "character_id": ch.id,
                    "name": ch.name,
                    "location_id": ch.location_id,
                    "kind": ch.kind,
                },
            )
        )
    return debts


# ── v2 stubs (COARSE narrative proxies — not implemented in v1) ───────────────

# TODO v2: setup_without_payoff — a quest_hook with status 'open' referenced in
# the first quarter of decisions but never advanced to 'active'/'resolved', and
# the campaign is past day N. Needs: stable creation-day on QuestHook (not yet in
# the model) or a first-Decision-day proxy. Coarse; advisory tolerates false-pos.

# TODO v2: act_structure — past the campaign midpoint (day > N/2 if there's an
# expected arc length) with no Decision.rationale containing reversal-tagged words
# ("betrayal", "revealed", "turned", "loss", "price"). Coarse; needs a defined
# arc-length on Campaign (not yet in the model). Advisory tolerates false-pos.


# ── Public API ────────────────────────────────────────────────────────────────


def detect(c: Campaign) -> list[SceneDebt]:
    """Detect structural scene debts from campaign state. Pure, no I/O.

    Returns a list of ``SceneDebt`` objects; empty means no debts detected
    (today's behavior). Old snapshots without ``scene_debts`` round-trip
    unchanged — this function reads state, never mutates it.

    The six debt kinds detected:
    - ``hook_untracked``: an engaged quest_hook with no tracked Quest.
    - ``quest_stalled``: an active Quest with no story beat in ≥ QUEST_STALL_DAYS.
    - ``choice_without_outcome``: a Decision offered (options present) but chosen=''.
    - ``due_consequence``: a non-thread Consequence past trigger_day, not fired.
    - ``thread_pressure``: a worldsim thread-beat overdue (world_tick not called).
    - ``npc_introduced_silent``: a met NPC at current location with no memory.
    """
    debts: list[SceneDebt] = []
    debts.extend(_detect_hook_untracked(c))
    debts.extend(_detect_quest_stalled(c))
    debts.extend(_detect_choice_without_outcome(c))
    debts.extend(_detect_due_consequence(c))
    debts.extend(_detect_thread_pressure(c))
    debts.extend(_detect_npc_introduced_silent(c))
    return debts
