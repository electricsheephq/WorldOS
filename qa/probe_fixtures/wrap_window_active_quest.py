#!/usr/bin/env python3
"""wrap_window_active_quest — the FIRST Tier-1.5 mechanism-probe fixture.

Builds a deterministic campaign parked at the #1334 endgame WRAP WINDOW with ONE thread the
engine wants closed, so a short live-beat probe can ask the cue-mechanism question directly:
"does the DM ACT on quest_endgame_unresolved (complete_quest / complete_objective) — or only
narrate?" — without paying for a 24-beat cold-opened duo to reach the wrap window naturally.

The parked state (all engine-written; the engine remains SOLE WRITER — this only calls
server.* + save_campaign, exactly like the qa/seed_gfx_*.py seeds):
  * a LIVING PC (Aldric, human fighter L4) seated + in the party — a real, alive combatant;
  * a companion (Neris, half-elf cleric L4) present in the party;
  * ONE active quest with an INCOMPLETE objective (the thread the wrap-window cue names);
  * narrative_arc set to act=2, beats_in_act=8 — the #1334 wrap window OPEN
    (_in_wrap_window = act >= 2 AND beats_in_act >= _WRAP_WINDOW_BEATS(=8)).

At that state _compute_beat_obligations escalates the active quest to a single HIGH
``quest_endgame_unresolved`` cue, which (severity-sorted first) becomes the beat's
``next_action`` — the deterministic pre-check this builder ASSERTS before returning (free,
no LLM). If that invariant ever breaks, the fixture fails LOUDLY here rather than shipping a
probe that measures nothing.

⚠ ITERATION SIGNAL ONLY — a seeded mid-arc fixture SKIPS the cold-open / seat-path / free-play
surfaces where our real bugs live (see docs/qa/FAST_GATE.md "the trap"). NEVER cite a probe
built on this fixture as release evidence.

Usage (WORLDOS_STATE_DIR is set by the caller; uv --directory cd's into servers/engine, so
pass this script by ABSOLUTE path):
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/probe_fixtures/wrap_window_active_quest.py" <state_dir>

Prints a one-line JSON manifest (campaign_id, pc_id, companion_id, quest_id, cue, next_action)
on the last line, for the probe runner to consume.
"""
import json
import os
import sys


# Pin a well-known campaign id + deterministic seats so "same fixture → same snapshot" holds
# (create_campaign auto-ids, so build the Campaign directly the way seed_gfx_combat.py does).
CID = "camp_probe_wrapwindow"
QUEST_TITLE = "The Silent Sending Stone"
QUEST_OBJECTIVES = [
    "Find the Harper's last known haunt",   # left INCOMPLETE → the active thread the cue names
    "Learn who silenced the stone",
]
# The wrap window opens at act >= 2 AND beats_in_act >= _WRAP_WINDOW_BEATS(=8). Park exactly on
# the floor (act=2, beats_in_act=8) — the minimal state that opens it, matching the task spec.
ARC_ACT = 2
ARC_BEATS_IN_ACT = 8


def _build(server) -> dict:
    from models import Campaign  # noqa: PLC0415

    # 1. A campaign + a location to stand in (so the scene has a place). Pin the id.
    server.save_campaign(Campaign(
        id=CID, title="Mechanism Probe — wrap-window active quest",
        summary="Tier-1.5 mechanism-probe fixture: parked at the #1334 endgame wrap window with one active quest.",
    ))
    server.add_location(
        campaign_id=CID, name="Lower City — Harper safehouse", make_current=True,
        description="A shuttered Harper safehouse in the Lower City; the sending stone on the table is dark and cold.",
    )

    server.start_session(CID, title="Mechanism Probe")

    # 2. A LIVING PC — a real, alive combatant (never a corpse; the wrap-window cue only fires on a
    #    living campaign). Deterministic create_character seat (not a canon pull) so the snapshot is
    #    reproducible byte-for-byte across runs; the probe measures the CUE mechanism, not seating.
    pc = server.create_character(
        campaign_id=CID, name="Aldric", kind="player",
        race="human", class_name="fighter", level=4,
        abilities={"strength": 16, "dexterity": 14, "constitution": 15,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    pc_id = pc["id"]

    # 3. A companion present in the party (the cue population requires a living PC + an active quest;
    #    a companion makes the parked state a real party the wrap-window scene can voice).
    comp = server.create_character(
        campaign_id=CID, name="Neris", kind="companion",
        race="half-elf", class_name="cleric", level=4,
        abilities={"strength": 10, "dexterity": 12, "constitution": 14,
                   "intelligence": 11, "wisdom": 16, "charisma": 13},
        apply_srd_defaults=True, add_to_party=True,
    )
    comp_id = comp["id"]

    # 4. ONE active quest with an INCOMPLETE objective — the thread the wrap-window cue escalates.
    q = server.add_quest(
        CID, QUEST_TITLE,
        description="A Harper contact's sending stone has gone quiet in the Lower City — someone should look in.",
        location_id=server._require(CID).current_location_id or "",
        objectives=list(QUEST_OBJECTIVES),
    )
    quest_id = q["id"]

    # 5. Park the narrative arc in the wrap window (act=2, beats_in_act=8). NarrativeArc is
    #    engine-written (persist_beat bumps beats_in_act; advance_act stamps the act) — but a
    #    DETERMINISTIC fixture sets it directly under the campaign the same way the gfx seeds
    #    author scene_grid / combat directly, then save_campaign persists it. This is the ONLY
    #    non-verb mutation, and it is the state we are deliberately reproducing.
    c = server._require(CID)
    c.narrative_arc.act = ARC_ACT
    c.narrative_arc.beats_in_act = ARC_BEATS_IN_ACT
    server.save_campaign(c)

    return {"campaign_id": CID, "pc_id": pc_id, "companion_id": comp_id, "quest_id": quest_id}


def _precheck(server, quest_id: str) -> dict:
    """The deterministic (free, no-LLM) pre-check: _compute_beat_obligations on the fixture must
    yield ``quest_endgame_unresolved`` as the beat's ``next_action``, naming our active quest.
    Returns the {cue, next_action} it observed; RAISES if the invariant is broken so the fixture
    can never ship a probe that measures nothing."""
    c = server._require(CID)
    obligations = server._compute_beat_obligations(c)
    kinds = [o.get("kind") for o in obligations]
    next_action = server._next_action(obligations)
    na_kind = (next_action or {}).get("kind")

    if "quest_endgame_unresolved" not in kinds:
        raise AssertionError(
            "pre-check FAILED: expected a quest_endgame_unresolved obligation in the wrap window, "
            f"got kinds={kinds}. The fixture no longer parks the #1334 wrap window — a probe on it "
            "would measure nothing."
        )
    if na_kind != "quest_endgame_unresolved":
        raise AssertionError(
            "pre-check FAILED: expected next_action.kind == 'quest_endgame_unresolved' (the HIGH "
            f"wrap-window cue must be the single top obligation), got {na_kind!r} from kinds={kinds}."
        )
    # The cue must name OUR active quest (so the probe's verdict reads the right thread).
    endgame = next(o for o in obligations if o.get("kind") == "quest_endgame_unresolved")
    if endgame.get("quest_id") != quest_id:
        raise AssertionError(
            f"pre-check FAILED: quest_endgame_unresolved names quest {endgame.get('quest_id')!r}, "
            f"expected the fixture's active quest {quest_id!r}."
        )
    return {"cue": "quest_endgame_unresolved", "next_action": na_kind, "obligation_kinds": kinds}


def build_and_precheck() -> dict:
    """Build the fixture into WORLDOS_STATE_DIR and run the deterministic pre-check.

    Importable (the pytest determinism + pre-check tests call this) as well as CLI-runnable.
    Returns the manifest dict merged with the pre-check observation. RAISES on a broken invariant.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "servers", "engine"))
    import server  # noqa: PLC0415

    manifest = _build(server)
    manifest.update(_precheck(server, manifest["quest_id"]))
    return manifest


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: wrap_window_active_quest.py <state_dir>", file=sys.stderr)
        return 2
    os.environ["WORLDOS_STATE_DIR"] = sys.argv[1]
    manifest = build_and_precheck()
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
