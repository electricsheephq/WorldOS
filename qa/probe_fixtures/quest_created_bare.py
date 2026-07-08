#!/usr/bin/env python3
"""quest_created_bare — the #1405(a) quest-AUTHORING mechanism-probe fixture.

Builds a deterministic campaign whose ONE active quest was created BARE — no objectives, no
giver, no location — so a short live-beat probe can ask the authoring-cue question directly:
"does the DM ACT on quest_authoring_incomplete (author the quest's spine) — or only narrate a
job with no trackable goals?" This is the flywheel's NEW binding constraint (#1405: objectives
empty in 2/3 sampled quests AT THE SNAPSHOT LEVEL — no engine data exists to extract).

The parked state (all engine-written; the engine remains SOLE WRITER — this only calls
server.* + save_campaign, exactly like wrap_window_active_quest.py / the qa/seed_gfx_*.py seeds):
  * a LIVING PC (Aldric, human fighter L4) seated + in the party — a real, alive protagonist;
  * ONE active quest created via add_quest WITHOUT objectives / giver_id / location_id — the
    spine-less thread the authoring cue names.

SOLO PC ON PURPOSE: the authoring cue is MED severity; an un-gauged recruited companion would
emit a competing MED companion_gauge_unauthored that (appended earlier) would outrank it as
next_action. A solo living PC keeps quest_authoring_incomplete the single top obligation the
probe's cue-check reads — the same discipline wrap_window_active_quest.py relies on for its
HIGH cue.

At that state _compute_beat_obligations yields a single MED ``quest_authoring_incomplete`` cue,
which becomes the beat's ``next_action`` — the deterministic pre-check this builder ASSERTS
before returning (free, no LLM). If that invariant ever breaks, the fixture fails LOUDLY here
rather than shipping a probe that measures nothing.

The probe's DETERMINISTIC verdict (qa/probe_verdict.py) reads "did engine state MOVE?" for this
cue as quest_authoring_progressed: an existing quest gained objectives/giver/location, OR a new
quest arrived carrying an objective spine (the realistic "DM re-called add_quest with the fields"
path — there is no update-fields tool). ACTED = cue present at start + the DM called add_quest /
set_quest_status + that authoring movement.

⚠ ITERATION SIGNAL ONLY — a seeded fixture SKIPS the cold-open / seat-path / free-play surfaces
where our real bugs live (see docs/qa/FAST_GATE.md "the trap"). NEVER cite a probe built on this
fixture as release evidence.

Usage (WORLDOS_STATE_DIR is set by the caller; uv --directory cd's into servers/engine, so pass
this script by ABSOLUTE path):
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/probe_fixtures/quest_created_bare.py" <state_dir>

Prints a one-line JSON manifest (campaign_id, pc_id, quest_id, cue, next_action) on the last
line, for the probe runner to consume.
"""
import json
import os
import sys


# Pin a well-known campaign id so "same fixture → same snapshot" holds (create_campaign
# auto-ids, so build the Campaign directly the way wrap_window_active_quest.py does).
CID = "camp_probe_questbare"
QUEST_TITLE = "A Word from the Docks"


def _build(server) -> dict:
    from models import Campaign  # noqa: PLC0415

    # 1. A campaign + a place to stand in (so the scene has a location). Pin the id.
    server.save_campaign(Campaign(
        id=CID, title="Mechanism Probe — bare quest authoring",
        summary="Tier-1.5 mechanism-probe fixture: one active quest created with no objectives/giver/location.",
    ))
    server.add_location(
        campaign_id=CID, name="Lower City — the docks", make_current=True,
        description="A rain-slick dock in the Lower City; a nervous longshoreman keeps glancing over his shoulder.",
    )

    server.start_session(CID, title="Mechanism Probe")

    # 2. A LIVING PC — a real, alive protagonist. Deterministic create_character seat (not a canon
    #    pull) so the snapshot is reproducible byte-for-byte across runs; the probe measures the
    #    CUE mechanism, not seating.
    pc = server.create_character(
        campaign_id=CID, name="Aldric", kind="player",
        race="human", class_name="fighter", level=4,
        abilities={"strength": 16, "dexterity": 14, "constitution": 15,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    pc_id = pc["id"]

    # 3. THE bare quest — created via add_quest with NO objectives, giver_id, or location_id. This
    #    is exactly the under-authored quest #1405 diagnoses (a job with no trackable spine). The
    #    engine verb is the sole writer; we deliberately reproduce the state the DM leaves behind.
    q = server.add_quest(CID, QUEST_TITLE, description="The longshoreman wants to hire the party for something.")
    quest_id = q["id"]

    # 4. Park the arc a few beats in (beats_in_act == _QUEST_AUTHORING_BEATS) so the per-beat
    #    quest_authoring_incomplete cue has ESCALATED — the quest was narrated a stretch and STILL
    #    has no spine (the create-moment nudge is the add_quest result cue; this is the escalation).
    #    Set directly the way wrap_window_active_quest.py parks its arc; save_campaign persists it.
    c = server._require(CID)
    c.narrative_arc.beats_in_act = server._QUEST_AUTHORING_BEATS
    server.save_campaign(c)

    return {"campaign_id": CID, "pc_id": pc_id, "quest_id": quest_id}


def _precheck(server, quest_id: str) -> dict:
    """The deterministic (free, no-LLM) pre-check: _compute_beat_obligations on the fixture must
    yield ``quest_authoring_incomplete`` as the beat's ``next_action``, naming our bare quest.
    RAISES if the invariant is broken so the fixture can never ship a probe that measures nothing."""
    c = server._require(CID)
    obligations = server._compute_beat_obligations(c)
    kinds = [o.get("kind") for o in obligations]
    next_action = server._next_action(obligations)
    na_kind = (next_action or {}).get("kind")

    if "quest_authoring_incomplete" not in kinds:
        raise AssertionError(
            "pre-check FAILED: expected a quest_authoring_incomplete obligation for the bare quest, "
            f"got kinds={kinds}. The fixture no longer parks an un-authored quest — a probe on it "
            "would measure nothing."
        )
    if na_kind != "quest_authoring_incomplete":
        raise AssertionError(
            "pre-check FAILED: expected next_action.kind == 'quest_authoring_incomplete' (the sole "
            f"top obligation on a solo-PC bare-quest fixture), got {na_kind!r} from kinds={kinds}."
        )
    authoring = next(o for o in obligations if o.get("kind") == "quest_authoring_incomplete")
    if authoring.get("quest_id") != quest_id:
        raise AssertionError(
            f"pre-check FAILED: quest_authoring_incomplete names quest {authoring.get('quest_id')!r}, "
            f"expected the fixture's bare quest {quest_id!r}."
        )
    return {"cue": "quest_authoring_incomplete", "next_action": na_kind, "obligation_kinds": kinds}


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
        print("usage: quest_created_bare.py <state_dir>", file=sys.stderr)
        return 2
    os.environ["WORLDOS_STATE_DIR"] = sys.argv[1]
    manifest = build_and_precheck()
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
