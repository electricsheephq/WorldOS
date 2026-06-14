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


def _quest_has_followon(c: Campaign, quest_id: str, quest_title: str) -> bool:
    """Return True if a resolved quest already has SOME payoff/echo wired: a
    scheduled Consequence that grew from it (the rule-of-three ``evolves_from:<id>``
    note, or any consequence whose text/note references the quest id/title), or a
    quest_hook that arcs back to it (by id reference in its fields). Used to tell a
    resolved-but-echoing thread from a resolved-and-forgotten one."""
    title_lower = (quest_title or "").lower()
    evolves_note = f"evolves_from:{quest_id}"
    for con in c.consequences:
        if con.note == evolves_note:
            return True
        blob = (con.text + " " + con.note).lower()
        if quest_id in blob or (title_lower and title_lower in blob):
            return True
    for h in c.quest_hooks:
        blob = (h.title + " " + h.arc_back + " " + h.note).lower()
        if quest_id in blob or (title_lower and title_lower in blob):
            return True
        if quest_id in h.prereq:
            return True
    return False


# ── structural detectors ──────────────────────────────────────────────────────


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
    """quest_stalled — an active Quest with no progress in >= QUEST_STALL_DAYS.

    F05-7: the PRIMARY signal is now the engine-mutated ``Quest.last_progress_day`` —
    stamped under the lock by add_quest / complete_objective / complete_quest /
    set_quest_status. A quest whose last progress was within the window is NOT stalled,
    so a quest added late no longer flags on the very next beat, and any of the engine's
    own progress verbs resets the clock (the detector reads engine state, never prose).

    Old-snapshot fallback: a quest with ``last_progress_day == -1`` (never stamped) keeps
    the legacy Decision-text proxy (a Decision referencing the quest id/title within the
    window counts as a callback), so old snapshots behave exactly as before.
    A brand-new campaign (day <= threshold) generates no debts."""
    if c.day <= QUEST_STALL_DAYS:
        return []
    debts: list[SceneDebt] = []
    for q in c.quests.values():
        if q.status != "active":
            continue
        lpd = getattr(q, "last_progress_day", -1)
        if lpd >= 0:
            # Engine-stamped: not stalled while the last progress is within the window.
            if c.day - lpd < QUEST_STALL_DAYS:
                continue
        else:
            # Old snapshot: fall back to the Decision-text proxy (today's behavior).
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


def _detect_quest_no_followon(c: Campaign) -> list[SceneDebt]:
    """thread_no_payoff — a RESOLVED Quest (status == 'completed') with NO follow-on:
    empty ``evolves_to`` AND no consequence/hook referencing it. The rule-of-three
    nudge: every thread should echo. ADVISORY only (severity low) — the Director
    surfaces it so the DM can set ``evolves_to`` / schedule a callback; the engine
    NEVER auto-acts. A quest that already evolves (``evolves_to`` set) or already has
    a payoff wired is NOT flagged."""
    debts: list[SceneDebt] = []
    for q in c.quests.values():
        if q.status != "completed":
            continue  # only resolved threads can lack a payoff
        if (q.evolves_to or "").strip():
            continue  # already evolves -> not a debt
        if _quest_has_followon(c, q.id, q.title):
            continue  # already echoed via a consequence/hook
        debts.append(
            SceneDebt(
                id=_debt_id("thread_no_payoff", q.id),
                kind="thread_no_payoff",
                subject=q.id,
                detail=(
                    f"Resolved thread '{q.title}' has no payoff/echo — consider a "
                    f"callback (set evolves_to) so it lingers instead of ending one-and-done."
                ),
                severity="low",
                evidence={"quest_id": q.id, "quest_title": q.title, "status": q.status},
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


def _detect_faction_rank_available(c: Campaign) -> list[SceneDebt]:
    """faction_rank_available — a JOINED faction whose questline (FactionArc) has a stage the
    party has EARNED (status ``available``) but not yet taken. The Skyrim/Kingmaker "your
    promotion is waiting" nudge: the gauge gate is satisfied, the rank-up is on the table, but the
    DM hasn't played the beat. ADVISORY only (severity low) — the Director surfaces it so the DM
    can play the rank-up and call ``advance_faction_arc``; the engine NEVER auto-advances a faction
    quest (map seam #5). Pure / read-only: it reports stages ALREADY in ``available`` (flipped by
    the engine's gauge eval); it never mutates an arc. A faction not yet joined, or an arc with no
    available stage, is not flagged."""
    import faction_arc as _fa  # local import: keep scene_debt's import surface minimal/pure

    debts: list[SceneDebt] = []
    for arc in sorted(c.faction_arcs.values(), key=lambda a: a.id):
        fac = c.factions.get(arc.faction_id)
        if fac is None:
            continue
        if arc.requires_joined and not fac.joined:
            continue  # not a member — no earned rank-up to nudge
        armed = fac.joined or not arc.requires_joined
        available = [s for s in arc.stages if s.status == "available"]
        # F05-6: a LOCKED stage whose gauge gate ALREADY HOLDS is "earned but not yet
        # unlocked" — invisible on every per-beat surface because nothing on the beat loop
        # calls evaluate() to flip it. Detect it READ-ONLY (stage_gate_holds is pure) so the
        # Director can nudge the DM to call check_faction_arcs (the flipper). We NEVER flip the
        # stage here (detect stays pure) and we NEVER claim it as available — the advisory must
        # not misstate engine state, so earned-but-locked is labeled DISTINCTLY.
        earned_locked = [
            s for s in arc.stages
            if armed and s.status == "locked" and _fa.stage_gate_holds(s, fac)
        ]
        if not available and not earned_locked:
            continue
        bits = []
        if available:
            bits.append("available now: " + ", ".join(s.title for s in available))
        if earned_locked:
            bits.append("EARNED (call check_faction_arcs to unlock): "
                        + ", ".join(s.title for s in earned_locked))
        debts.append(
            SceneDebt(
                id=_debt_id("faction_rank_available", arc.id),
                kind="faction_rank_available",
                subject=arc.id,
                detail=(
                    f"Faction questline '{arc.title}' ({fac.name}) has a rank-up the party earned "
                    f"but hasn't taken — {'; '.join(bits)}. "
                    f"Play the promotion / next mission and call advance_faction_arc "
                    f"(if EARNED-but-locked, call check_faction_arcs first to unlock it)."
                ),
                severity="low",
                evidence={
                    "arc_id": arc.id,
                    "faction_id": arc.faction_id,
                    "faction_name": fac.name,
                    "available_stage_ids": [s.id for s in available],
                    # DISTINCT key — never put a locked stage in available_stage_ids.
                    "earned_locked_stage_ids": [s.id for s in earned_locked],
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

    The debt kinds detected:
    - ``hook_untracked``: an engaged quest_hook with no tracked Quest.
    - ``quest_stalled``: an active Quest with no story beat in ≥ QUEST_STALL_DAYS.
    - ``thread_no_payoff``: a resolved Quest with no follow-on (rule-of-three nudge).
    - ``choice_without_outcome``: a Decision offered (options present) but chosen=''.
    - ``due_consequence``: a non-thread Consequence past trigger_day, not fired.
    - ``thread_pressure``: a worldsim thread-beat overdue (world_tick not called).
    - ``npc_introduced_silent``: a met NPC at current location with no memory.
    - ``faction_rank_available``: a joined faction's questline has an earned, untaken rank-up.
    """
    debts: list[SceneDebt] = []
    debts.extend(_detect_hook_untracked(c))
    debts.extend(_detect_quest_stalled(c))
    debts.extend(_detect_quest_no_followon(c))
    debts.extend(_detect_choice_without_outcome(c))
    debts.extend(_detect_due_consequence(c))
    debts.extend(_detect_thread_pressure(c))
    debts.extend(_detect_npc_introduced_silent(c))
    debts.extend(_detect_faction_rank_available(c))
    return debts


# A resolved debt stays SUPPRESSED for this many in-world days after resolution (F05-4).
# After the snooze lapses, if the SAME structural fact is still detected, it re-surfaces
# (the world genuinely still owes it) and can be re-resolved. A non-positive value means
# "suppress forever once resolved" — we use a finite snooze so a chronic, unaddressed fact
# is not silenced permanently by a single stale resolution.
RESOLVED_SNOOZE_DAYS: int = 7


def is_snoozed(rec: SceneDebt, day: int) -> bool:
    """Whether a resolved debt record should still suppress its live re-detection on
    ``day``. Suppresses while within the snooze window from ``resolved_day``. A record
    with no ``resolved_day`` (-1, e.g. an old snapshot) suppresses unconditionally — the
    only signal we have is ``resolved=True``, and the pre-F05-4 contract treated any
    resolved record as cleared."""
    if not rec.resolved:
        return False
    rd = getattr(rec, "resolved_day", -1)
    if rd < 0:
        return True  # old record: no day to age against -> honor the resolution
    return day - rd < RESOLVED_SNOOZE_DAYS


def live(c: Campaign) -> list[SceneDebt]:
    """The DM-facing LIVE debts: ``detect(c)`` with already-resolved (and still-snoozed)
    debts SUPPRESSED (F05-4).

    ``detect`` stays PURE and unchanged (every detector re-derives the same structural
    fact under the same deterministic id). This wrapper drops any detected debt whose id
    matches a resolved record on ``c.scene_debts`` that is still within its snooze window —
    so a debt the DM cleared with ``resolve_scene_debt`` stops re-surfacing on every beat
    and stops crowding out the Director's top-3 slots. Once the snooze lapses, an
    un-addressed structural fact re-detects and can be re-resolved (no eternal silence,
    no eternal nag). Read-only: no mutation, no I/O."""
    suppressed = {
        d.id for d in c.scene_debts if d.resolved and is_snoozed(d, c.day)
    }
    return [d for d in detect(c) if d.id not in suppressed]
