"""Companion deliberation / advice frame for the story loop (P3.5)."""

import pytest

import companion
import server
from models import Character


def test_deliberate_frame_carries_voice_personality_callbacks_not_words():
    comp = Character(
        name="Vesper", kind="companion", voice_id="companion-default",
        personality="a warm field medic who argues for mercy",
    )
    frame = companion.deliberate(
        comp, situation="a cornered goblin begs for its life",
        callbacks=[{"text": "last time we showed mercy it paid off"}],
    )
    assert frame["companion"] == "Vesper" and frame["voice_id"] == "companion-default"
    assert "mercy" in frame["personality"]
    assert frame["callbacks"] == ["last time we showed mercy it paid off"]
    # the frame is an instruction to voice — it never invents the companion's words
    assert "Vesper" in frame["prompt"] and "opinion" in frame["prompt"].lower()


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.start_adventure("embergloom-pact")["campaign_id"]


def test_companion_advise_grounds_in_recalled_memory(cid):
    server.log_event(cid, "narration", "Sister Velandra smiled too kindly at the dying acolyte.")
    comp_id = next(p["id"] for p in server.get_state(cid)["party"] if p["kind"] == "companion")
    out = server.companion_advise(cid, comp_id, situation="Velandra acolyte")
    assert out["voice_id"] and "prompt" in out
    assert any("Velandra" in cb for cb in out["callbacks"])  # recall surfaced the beat
