"""Regression (audit 2026-06-18) — seed_campaign folds a top-level `companion_quest_arcs` block.

THE BUG (HOLLOW-MILE QUEST-LINK DANGLES): `seed_campaign` folded locations / npcs / companions
(+ their CompanionArc gates) / factions / hook, but had NO handling of a top-level
`companion_quest_arcs` block. The authored hollow-mile campaign ships Doctor Eline Mourn with a
`personal_quest` arc-gate that links `quest_arc_id='cqarc-eline-stillwater'` — but nothing seeded
that arc, so the link dangled FOREVER at runtime (F06-11 latches a one-shot `link_error` and the
gate stays locked-but-recoverable). The CompanionQuestArc content path existed only for
world/ending seeds (`_seed_companion_quest_arcs`), never for an authored adventure module.

THE FIX: `seed_campaign` now folds `adv['companion_quest_arcs']` via the same loader the
world/overlay paths use (`_seed_companion_quest_arcs_block`) — AFTER the companions loop (so the
arc's `companion_id` owner exists + is a companion) and AFTER quest seeding (so any `quest_ids`
projection ref-checks). Additive + degrade-not-abort.

These tests guard:
  * seed_campaign('hollow-mile') seats the `cqarc-eline-stillwater` arc keyed by id, owned by the
    real companion, validated through the model.
  * the personal_quest gate's `quest_arc_id` RESOLVES to the seated arc (no dangle) — the engine's
    own `_unlock_companion_quest_arc` returns an availability transition, not an `error`.
  * the fold is additive: an adventure with no `companion_quest_arcs` key seeds nothing.

Single-process only (the host OOMs on parallel pytest; never -n / xdist).
"""

import content as content_mod
from companion_arc import _unlock_companion_quest_arc


def test_seed_campaign_seats_companion_quest_arc_hollow_mile():
    """The authored top-level `companion_quest_arcs` block is folded onto the Campaign, keyed by
    id, owned by the roster companion, and round-tripped through CompanionQuestArc."""
    adv = content_mod.load_adventure_data("hollow-mile")
    c = content_mod.seed_campaign(adv)

    arc = c.companion_quest_arcs.get("cqarc-eline-stillwater")
    assert arc is not None, "seed_campaign must fold the top-level companion_quest_arcs block"
    assert arc.companion_id == "companion-eline"
    assert arc.title == "The Cure That Became a Curse"
    # the owner exists in the roster and is a companion (the ref-check the loader enforces)
    owner = c.characters.get(arc.companion_id)
    assert owner is not None and owner.kind == "companion"
    # the authored stage the personal_quest gate makes available survived the fold
    assert any(s.id == "cqstage-eline-confession" for s in arc.stages)


def test_seed_campaign_personal_quest_gate_link_resolves_no_dangle():
    """Eline's personal_quest gate links quest_arc_id 'cqarc-eline-stillwater'. After the fold, the
    engine's own resolution path resolves it to the seated arc/stage with NO link error — proving
    the gate no longer dangles."""
    adv = content_mod.load_adventure_data("hollow-mile")
    c = content_mod.seed_campaign(adv)

    comp = c.characters["companion-eline"]
    assert comp.arc is not None
    gate = next(g for g in comp.arc.arc_gates if g.kind == "personal_quest")
    assert gate.quest_arc_id == "cqarc-eline-stillwater"

    event = _unlock_companion_quest_arc(comp, c, gate)
    assert event is not None
    # the load-bearing assertion: the link resolves cleanly — no "no companion quest arc ..." dangle
    assert "error" not in event, f"gate link still dangles: {event.get('error')!r}"
    assert event["quest_arc_id"] == "cqarc-eline-stillwater"
    assert event["stage_id"] == "cqstage-eline-confession"
    # the gate surfaces the arc + its named stage as available (a real transition, not a no-op)
    assert event.get("status") == "available"
    assert event.get("stage_status") == "available"


def test_seed_campaign_without_companion_quest_arcs_block_is_a_noop():
    """The fold is additive: an adventure dict with no `companion_quest_arcs` key seeds nothing
    (today's behavior for every campaign that doesn't author one)."""
    adv = {
        "title": "No-arc adventure",
        "premise": "x",
        "companions": [{"id": "comp-1", "name": "Aide"}],
    }
    c = content_mod.seed_campaign(adv)
    assert c.companion_quest_arcs == {}
