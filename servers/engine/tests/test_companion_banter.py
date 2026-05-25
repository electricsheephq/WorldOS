"""#69 -- persistent camp beat state + deterministic banter scheduling."""

import json

import companion_banter
import server
import store
from models import Campaign, Character, CompanionAgenda, CompanionArc, CompanionDossier


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
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
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


def test_recorded_pair_key_is_stable_across_save_load(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
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


def test_camp_scene_does_not_expose_sealed_agenda_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
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
