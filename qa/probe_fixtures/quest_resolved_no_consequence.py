#!/usr/bin/env python3
"""quest_resolved_no_consequence — the #1405(b) consequence-CAPTURE mechanism-probe fixture.

Builds a deterministic campaign whose ONE quest RESOLVED (status=completed) with NO branch
outcome recorded — empty evolves_to AND no Consequence naming it — so a short live-beat probe can
ask the capture-cue question directly: "does the DM ACT to capture a resolved quest's
consequence (complete_quest(evolves_to=…) / add_consequence / record_decision) — or leave the win
a dead end?" This is the flywheel's other measured gap (#1405: consequences empty in 3/3 sampled
quests AT THE SNAPSHOT LEVEL — the promised dilemmas never actually branch in the engine).

The seeded cue is the EXISTING ``quest_no_echo`` beat obligation (a RESOLVED quest with empty
evolves_to and no naming consequence) — #1405(b) reuses it as the capture cue's per-beat surface
rather than adding a parallel obligation kind, and the resolution-time TOOL results
(complete_quest / complete_objective / record_decision) additionally carry the same nudge as a
``consequence_cue`` payload.

The parked state (all engine-written; engine = SOLE WRITER — only server.* + save_campaign):
  * a LIVING PC (Aldric, human fighter L4) seated + in the party;
  * ONE quest with every objective completed and status flipped to `completed`, evolves_to left
    EMPTY, and NO consequence in the campaign that names it — the dead-end win the cue flags.

SOLO PC ON PURPOSE: quest_no_echo is LOW severity; any MED companion cue (e.g.
companion_gauge_unauthored on an un-gauged recruit) would outrank it as next_action. A solo
living PC keeps quest_no_echo the single top obligation the probe's cue-check reads.

At that state _compute_beat_obligations yields a single LOW ``quest_no_echo`` cue → the beat's
``next_action`` — the deterministic pre-check this builder ASSERTS (free, no LLM). The probe's
verdict reads movement for this cue as quest_echo_captured: a new consequence was recorded OR a
quest gained a non-empty evolves_to. ACTED = cue present at start + the DM called
complete_quest / add_consequence / record_decision + that capture movement.

⚠ ITERATION SIGNAL ONLY — a seeded fixture SKIPS the cold-open / seat-path / free-play surfaces
where our real bugs live (docs/qa/FAST_GATE.md "the trap"). NEVER cite a probe on it as release
evidence.

Usage (WORLDOS_STATE_DIR is set by the caller; pass this script by ABSOLUTE path):
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/probe_fixtures/quest_resolved_no_consequence.py" <state_dir>

Prints a one-line JSON manifest (campaign_id, pc_id, quest_id, cue, next_action) on the last line.
"""
import json
import os
import sys


CID = "camp_probe_questnoconseq"
QUEST_TITLE = "The Silenced Bell"
QUEST_OBJECTIVES = ["Find who muffled the temple bell", "Confront the saboteur"]


def _build(server) -> dict:
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(
        id=CID, title="Mechanism Probe — resolved quest, no consequence",
        summary="Tier-1.5 mechanism-probe fixture: one completed quest with empty evolves_to and no naming consequence.",
    ))
    server.add_location(
        campaign_id=CID, name="Temple ward", make_current=True,
        description="The temple ward, quiet again now the bell hangs silent and the saboteur is caught.",
    )

    server.start_session(CID, title="Mechanism Probe")

    pc = server.create_character(
        campaign_id=CID, name="Aldric", kind="player",
        race="human", class_name="fighter", level=4,
        abilities={"strength": 16, "dexterity": 14, "constitution": 15,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    pc_id = pc["id"]

    # A quest whose objectives were all cleared, then resolved via complete_objective — the LAST
    # objective auto-resolves it to `completed` WITHOUT setting evolves_to (that path only sets the
    # rule-of-three fields when the DM passes them). So the quest lands `completed` + empty
    # evolves_to + no consequence: exactly the dead-end win quest_no_echo flags. All engine verbs.
    q = server.add_quest(
        CID, QUEST_TITLE,
        description="Someone muffled the great temple bell on the eve of the festival.",
        location_id=server._require(CID).current_location_id or "",
        objectives=list(QUEST_OBJECTIVES),
    )
    quest_id = q["id"]
    for obj in QUEST_OBJECTIVES:
        server.complete_objective(CID, quest_id, obj)

    return {"campaign_id": CID, "pc_id": pc_id, "quest_id": quest_id}


def _precheck(server, quest_id: str) -> dict:
    """The deterministic (free, no-LLM) pre-check: the quest must be `completed` with empty
    evolves_to, and _compute_beat_obligations must yield ``quest_no_echo`` as the beat's
    ``next_action`` naming it. RAISES on a broken invariant."""
    c = server._require(CID)
    q = c.quests[quest_id]
    if getattr(q, "status", None) != "completed":
        raise AssertionError(
            f"pre-check FAILED: expected the quest `completed` (all objectives cleared), got "
            f"status={getattr(q, 'status', None)!r}."
        )
    if (getattr(q, "evolves_to", "") or "").strip():
        raise AssertionError(
            "pre-check FAILED: the quest already has a non-empty evolves_to — the fixture must park "
            "a CONSEQUENCE-LESS resolution or the capture cue never fires."
        )
    obligations = server._compute_beat_obligations(c)
    kinds = [o.get("kind") for o in obligations]
    next_action = server._next_action(obligations)
    na_kind = (next_action or {}).get("kind")

    if "quest_no_echo" not in kinds:
        raise AssertionError(
            "pre-check FAILED: expected a quest_no_echo obligation for the resolved-no-consequence "
            f"quest, got kinds={kinds}. The fixture no longer parks a dead-end win."
        )
    if na_kind != "quest_no_echo":
        raise AssertionError(
            "pre-check FAILED: expected next_action.kind == 'quest_no_echo' (the sole top obligation "
            f"on a solo-PC resolved-no-consequence fixture), got {na_kind!r} from kinds={kinds}."
        )
    echo = next(o for o in obligations if o.get("kind") == "quest_no_echo")
    if echo.get("quest_id") != quest_id:
        raise AssertionError(
            f"pre-check FAILED: quest_no_echo names quest {echo.get('quest_id')!r}, expected the "
            f"fixture's resolved quest {quest_id!r}."
        )
    return {"cue": "quest_no_echo", "next_action": na_kind, "obligation_kinds": kinds}


def build_and_precheck() -> dict:
    """Build the fixture into WORLDOS_STATE_DIR and run the deterministic pre-check. Importable
    (the pytest tests call this) as well as CLI-runnable. RAISES on a broken invariant."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "servers", "engine"))
    import server  # noqa: PLC0415

    manifest = _build(server)
    manifest.update(_precheck(server, manifest["quest_id"]))
    return manifest


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: quest_resolved_no_consequence.py <state_dir>", file=sys.stderr)
        return 2
    os.environ["WORLDOS_STATE_DIR"] = sys.argv[1]
    manifest = build_and_precheck()
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
