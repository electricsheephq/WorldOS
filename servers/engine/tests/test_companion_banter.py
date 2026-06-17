"""#69 -- persistent camp beat state + deterministic banter scheduling."""

import json

import pytest

import companion_banter
import server
import store
from models import (
    Campaign,
    CampBeatRecord,
    Character,
    CompanionAgenda,
    CompanionArc,
    CompanionDossier,
)


def _campaign_with(*members: Character, day: int = 1) -> Campaign:
    c = Campaign(title="Camp Beats")
    c.day = day
    for member in members:
        c.characters[member.id] = member
        c.party.append(member.id)
    return c


def _companion(name: str, **kw) -> Character:
    max_hp = kw.pop("max_hp", 12)
    current_hp = kw.pop("current_hp", max_hp)
    return Character(name=name, kind="companion", max_hp=max_hp, current_hp=current_hp, **kw)


def test_old_campaign_snapshot_without_camp_beat_state_loads():
    c = _campaign_with(_companion("Vesper"))
    raw = c.model_dump(mode="json")
    raw.pop("camp_beats", None)

    reloaded = Campaign.model_validate(raw)

    assert reloaded.camp_beats.records == []


def test_scheduler_excludes_pc_and_dead_companions_from_pair_banter():
    pc = Character(name="Hero", kind="player")
    alive = _companion("Vesper")
    dead = _companion("Ash", dead=True, current_hp=0)
    c = _campaign_with(pc, alive, dead)

    beats = companion_banter.schedule_camp_beats(c, max_beats=8)

    assert all(pc.id not in beat.companion_ids for beat in beats)
    assert all(dead.id not in beat.companion_ids for beat in beats)
    assert not any(beat.kind == "pair_banter" for beat in beats)

    dead.dead = False
    dead.current_hp = 5
    beats = companion_banter.schedule_camp_beats(c, max_beats=8)
    pair = next(beat for beat in beats if beat.kind == "pair_banter")
    assert pair.companion_ids == sorted([alive.id, dead.id])
    assert pair.pair_key == companion_banter.pair_key(alive.id, dead.id)


def test_camp_scene_is_read_only_until_explicit_record_call(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Readonly Camp")["id"]
    pc = server.create_character(cid, "Hero", kind="player")["id"]
    first = server.create_character(cid, "Vesper", kind="companion")["id"]
    second = server.create_character(cid, "Ash", kind="companion")["id"]
    c = store.load_campaign(cid)
    c.characters[first].companion_dossier = CompanionDossier(
        camp_prompts=["asks what the party learned from the ruin"],
        banter_tags=["ruin"],
    )
    c.characters[second].companion_dossier = CompanionDossier(banter_tags=["oath"])
    store.save_campaign(c)
    before = json.loads((tmp_path / "campaigns" / cid / "snapshot.json").read_text())

    first_scene = server.camp_scene(cid)
    second_scene = server.camp_scene(cid)
    after = json.loads((tmp_path / "campaigns" / cid / "snapshot.json").read_text())

    assert first_scene["beats"] == second_scene["beats"]
    assert before == after
    assert store.load_campaign(cid).camp_beats.records == []
    assert all(pc not in beat["companion_ids"] for beat in first_scene["beats"])

    recorded = server.record_camp_beat(cid, first_scene["beats"][0]["beat_id"])
    assert recorded["record"]["id"] == first_scene["beats"][0]["beat_id"]
    assert store.load_campaign(cid).camp_beats.records

    cooled = server.camp_scene(cid)
    assert first_scene["beats"][0]["beat_id"] not in {beat["beat_id"] for beat in cooled["beats"]}


def test_record_camp_beat_rejects_explicit_replay_during_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Replay Guard")["id"]
    server.create_character(cid, "Vesper", kind="companion")
    beat = server.camp_scene(cid)["beats"][0]

    server.record_camp_beat(cid, beat["beat_id"])
    with pytest.raises(ValueError, match="cooldown|already recorded"):
        server.record_camp_beat(cid, beat["beat_id"], companion_ids=beat["companion_ids"])

    records = store.load_campaign(cid).camp_beats.records
    assert len(records) == 1
    assert records[0].id == beat["beat_id"]


def test_record_camp_beat_compacts_latest_per_key_and_caps_history(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Compact Camp")["id"]
    comp = server.create_character(cid, "Vesper", kind="companion")["id"]
    c = store.load_campaign(cid)
    c.camp_beats.max_records = 3
    store.save_campaign(c)

    beat = server.camp_scene(cid)["beats"][0]
    server.record_camp_beat(cid, beat["beat_id"])
    c = store.load_campaign(cid)
    c.day += c.camp_beats.solo_cooldown_days
    store.save_campaign(c)

    second = server.record_camp_beat(cid, beat["beat_id"], companion_ids=beat["companion_ids"])
    records = store.load_campaign(cid).camp_beats.records
    assert second["history_count"] == 1
    assert len(records) == 1
    assert records[0].id == beat["beat_id"]
    assert records[0].day == c.day

    for ix in range(5):
        server.record_camp_beat(cid, f"camp:manual:{ix}", companion_ids=[comp], kind="solo")

    records = store.load_campaign(cid).camp_beats.records
    assert len(records) == 3
    assert [record.id for record in records] == ["camp:manual:2", "camp:manual:3", "camp:manual:4"]


def test_recorded_pair_key_is_stable_across_save_load(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Pair Key")["id"]
    a = server.create_character(cid, "Vesper", kind="companion")["id"]
    b = server.create_character(cid, "Ash", kind="companion")["id"]
    pair = next(
        beat for beat in companion_banter.schedule_camp_beats(store.load_campaign(cid), max_beats=8)
        if beat.kind == "pair_banter"
    )

    out = server.record_camp_beat(cid, pair.beat_id)
    reloaded = store.load_campaign(cid)
    record = reloaded.camp_beats.records[0]

    assert out["record"]["pair_key"] == companion_banter.pair_key(a, b)
    assert record.pair_key == companion_banter.pair_key(a, b)
    assert Campaign.model_validate_json(reloaded.model_dump_json()).camp_beats.records[0].pair_key == record.pair_key


# --- F06-5b: the solo scheduler ROTATES through camp_prompts (not camp_prompts[0] forever) ---

def test_solo_candidate_rotates_camp_prompts_as_history_accrues():
    """F06-5 leg (b) (audit 2026-06-11): `_dossier_hooks` hard-indexed `camp_prompts[0]` with a
    constant anchor → constant cooldown key → the SAME prompt forever after cooldown, leaving
    prompts 1..N dead content. The solo candidate must advance through the authored prompts as
    this companion's solo beats are recorded, so each fresh camp visit voices a NEW hook."""
    comp = _companion(
        "Vesper",
        companion_dossier=CompanionDossier(
            camp_prompts=["asks about the ruin", "broods on the oath", "shares a fear of the dark"],
            banter_tags=["ruin"],
        ),
    )
    c = _campaign_with(comp)

    first = companion_banter._solo_candidate(comp, c)
    assert "asks about the ruin" in first.prompt

    # Record one solo beat for this companion -> the next visit advances to prompt[1].
    c.camp_beats.records.append(
        CampBeatRecord(id=first.beat_id, day=c.day, companion_ids=[comp.id], kind="solo",
                       cooldown_key=first.cooldown_key)
    )
    second = companion_banter._solo_candidate(comp, c)
    assert "broods on the oath" in second.prompt
    assert second.beat_id != first.beat_id          # a new beat id -> not on cooldown
    assert second.cooldown_key != first.cooldown_key

    # A third recorded beat advances to prompt[2].
    c.camp_beats.records.append(
        CampBeatRecord(id=second.beat_id, day=c.day, companion_ids=[comp.id], kind="solo",
                       cooldown_key=second.cooldown_key)
    )
    third = companion_banter._solo_candidate(comp, c)
    assert "shares a fear of the dark" in third.prompt


def test_solo_candidate_wraps_after_exhausting_prompts():
    """Rotation wraps modulo the prompt count, so a long campaign keeps cycling rather than
    sticking on the last prompt (still deterministic per recorded count)."""
    comp = _companion(
        "Vesper",
        companion_dossier=CompanionDossier(camp_prompts=["p0", "p1"], banter_tags=["t"]),
    )
    c = _campaign_with(comp)
    for _ in range(2):  # two recorded solos -> index 2 -> wraps to prompt[0]
        cand = companion_banter._solo_candidate(comp, c)
        c.camp_beats.records.append(
            CampBeatRecord(id=cand.beat_id, day=c.day, companion_ids=[comp.id], kind="solo",
                           cooldown_key=cand.cooldown_key)
        )
    wrapped = companion_banter._solo_candidate(comp, c)
    assert "p0" in wrapped.prompt


# --- F06-5c: pair banter is REACHABLE — camp_scene does not structurally starve it ----------

def test_camp_scene_surfaces_pair_banter_with_multiple_companions(tmp_path, monkeypatch):
    """F06-5 leg (c) (audit 2026-06-11): camp_scene passed `max_beats=len(companions)` while solo
    priorities (50-90) always outranked pair priorities (40+len(tags) <= 43), so the pair beats
    were ALWAYS sorted past the truncation point — pair banter was structurally unreachable. With
    two companions, a camp scene must surface at least one pair_banter beat."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Pair Reach")["id"]
    server.create_character(cid, "Hero", kind="player")
    a = server.create_character(cid, "Vesper", kind="companion")["id"]
    b = server.create_character(cid, "Ash", kind="companion")["id"]
    c = store.load_campaign(cid)
    c.characters[a].companion_dossier = CompanionDossier(banter_tags=["oath", "ruin"])
    c.characters[b].companion_dossier = CompanionDossier(banter_tags=["oath", "sea"])
    store.save_campaign(c)

    scene = server.camp_scene(cid)
    kinds = {beat["kind"] for beat in scene["beats"]}
    assert "pair_banter" in kinds, f"no pair banter surfaced: {[b['kind'] for b in scene['beats']]}"
    # and each living companion still gets their solo moment too (the round isn't pair-only).
    assert "solo" in kinds


def test_scene_durable_surfaces_camp_affordance_when_companions_present(tmp_path, monkeypatch):
    """F06-5 leg (a) (audit 2026-06-11): the camp pillar was unreachable — its only pointer was
    long_rest's camp_hint and the every-beat re-ground (scene_context.durable) had NO camp
    affordance. Surface a camp_available advisory when living companions are present and out of
    combat, so a re-grounding DM learns camp_scene exists."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Camp Reach")["id"]
    server.create_character(cid, "Hero", kind="player")
    server.create_character(cid, "Vesper", kind="companion")
    durable = server._scene_durable_threads(store.load_campaign(cid))
    assert "camp_available" in durable
    assert "Vesper" in durable["camp_available"]["companions"]


def test_scene_durable_omits_camp_affordance_for_solo_run(tmp_path, monkeypatch):
    """A party with NO companions has no camp affordance — today's durable shape byte-for-byte."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Solo")["id"]
    server.create_character(cid, "Hero", kind="player")
    durable = server._scene_durable_threads(store.load_campaign(cid))
    assert "camp_available" not in durable


def test_camp_scene_gathers_de_facto_companions_not_in_party(tmp_path, monkeypatch):
    """F06-5 (audit 2026-06-11, U06 line 994): camp was the ONE seam that gated on c.party
    while the relocate sweep (#353) + XP split (#739) include any kind=='companion'. A canon
    companion present in the roster but NOT formally added to c.party (a de-facto companion)
    walks WITH the party and must breathe at camp WITH them — present + scheduled a solo beat."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("De-facto Camp")["id"]
    server.create_character(cid, "Hero", kind="player")
    # A de-facto companion: in the roster, kind='companion', but never added to c.party.
    c = store.load_campaign(cid)
    ghost = Character(name="Shadowheart", kind="companion", max_hp=12, current_hp=12)
    c.characters[ghost.id] = ghost  # deliberately NOT appended to c.party
    store.save_campaign(c)
    assert ghost.id not in store.load_campaign(cid).party  # confirm it's de-facto

    scene = server.camp_scene(cid)
    assert "Shadowheart" in scene["present"]
    assert any(ghost.id in beat["companion_ids"] for beat in scene["beats"])


def test_camp_scene_does_not_expose_sealed_agenda_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Private Agenda")["id"]
    comp = server.create_character(cid, "Vesper", kind="companion")["id"]
    c = store.load_campaign(cid)
    c.characters[comp].arc = CompanionArc(
        agenda=CompanionAgenda(trigger="day_reached", value=99, note="SEALED BETRAYAL NOTE")
    )
    store.save_campaign(c)

    scene = server.camp_scene(cid)

    assert "SEALED BETRAYAL NOTE" not in json.dumps(scene)
    assert all("final dialogue" not in beat["prompt"].lower() for beat in scene["beats"])
