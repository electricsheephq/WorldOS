import recap
import store
from models import SessionLogEntry


def _entries() -> list[SessionLogEntry]:
    return [
        SessionLogEntry(t=1.0, kind="narration", text="The party entered the Whispering Caverns."),
        SessionLogEntry(t=2.0, kind="dialogue", text="Stay close to me.", speaker="Lyra"),
        SessionLogEntry(t=3.0, kind="roll", text="Stealth check: 17 vs DC 12 (success)."),
        SessionLogEntry(t=4.0, kind="system", text="Advanced to morning of day 2."),
        SessionLogEntry(t=5.0, kind="combat", text="A pack of goblins ambushed the heroes."),
    ]


def test_format_recap_mentions_key_beats():
    out = recap.format_recap(_entries())
    assert isinstance(out, str)
    assert out.strip()
    assert out.startswith("Previously on your adventure...")
    # Story beats are present.
    assert "Whispering Caverns" in out
    assert "Stay close to me." in out
    assert "Lyra" in out
    assert "goblins" in out


def test_format_recap_ignores_noise():
    out = recap.format_recap(_entries())
    # Roll + system bookkeeping is dropped from the narrative recap.
    assert "Stealth check" not in out
    assert "Advanced to morning" not in out


def test_format_recap_empty_log():
    out = recap.format_recap([])
    assert isinstance(out, str)
    assert out.strip()
    assert "start of a new adventure" in out.lower()


def test_format_recap_only_noise_is_new_adventure():
    noise = [
        SessionLogEntry(t=1.0, kind="roll", text="d20: 14"),
        SessionLogEntry(t=2.0, kind="system", text="Short rest taken."),
    ]
    out = recap.format_recap(noise)
    assert "start of a new adventure" in out.lower()


def test_format_recap_respects_max_entries():
    entries = [
        SessionLogEntry(t=float(i), kind="narration", text=f"Beat number {i}.")
        for i in range(20)
    ]
    out = recap.format_recap(entries, max_entries=3)
    # Only the most recent 3 story beats survive.
    assert "Beat number 19." in out
    assert "Beat number 18." in out
    assert "Beat number 17." in out
    assert "Beat number 16." not in out
    assert "Beat number 0." not in out


def test_format_recap_dialogue_without_speaker():
    out = recap.format_recap(
        [SessionLogEntry(t=1.0, kind="dialogue", text="Who goes there?")]
    )
    assert "Who goes there?" in out
    assert out.startswith("Previously on your adventure...")


# ── F07-1: schema-stamped combat bookkeeping is decontaminated from the recap ──
# (issue #772). The cold-open "previously on" must recite STORY, not the engine's
# mechanical combat-event rows (`_log_combat_event` stamps payload schema
# clawdnd.combat_event.v1). A NARRATIVE combat beat (no schema-stamped payload)
# still survives — that is the existing goblins line above.

_COMBAT_EVENT_SCHEMA = "clawdnd.combat_event.v1"


def test_format_recap_drops_schema_stamped_combat_bookkeeping():
    entries = [
        SessionLogEntry(t=1.0, kind="narration", text="The party kicked in the cellar door."),
        # Engine bookkeeping rows — mechanical, schema-stamped. Must NOT recite.
        SessionLogEntry(
            t=2.0, kind="combat", text="Tough 1 takes 5 force damage (12 -> 7).",
            payload={"schema": _COMBAT_EVENT_SCHEMA, "target": "tough-1", "damage": 5},
        ),
        SessionLogEntry(
            t=3.0, kind="combat", text="Turn advances to Tough 2.",
            payload={"schema": _COMBAT_EVENT_SCHEMA, "current": "tough-2"},
        ),
        # A narrative combat beat (no schema-stamped payload) IS story — keep it.
        SessionLogEntry(t=4.0, kind="combat", text="The ogre roared and the floor shook."),
    ]
    out = recap.format_recap(entries)
    assert "cellar door" in out
    assert "ogre roared" in out
    # Mechanical bookkeeping is gone.
    assert "force damage" not in out
    assert "Turn advances" not in out


def test_format_recap_keeps_combat_without_payload():
    # A combat row with payload=None is a narrative beat and must survive (guards the
    # existing goblins-ambush line semantics).
    out = recap.format_recap(
        [SessionLogEntry(t=1.0, kind="combat", text="A pack of goblins ambushed the heroes.")]
    )
    assert "goblins" in out


def test_format_recap_keeps_combat_with_unrelated_payload():
    # A combat row carrying a payload that is NOT the combat-event schema is still story.
    out = recap.format_recap(
        [SessionLogEntry(
            t=1.0, kind="combat", text="The duel ended at the river's edge.",
            payload={"mood": "tense"},
        )]
    )
    assert "duel ended" in out


def test_recap_from_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    campaign_id = "camp_test123"
    session_id = "sess_test123"

    # Empty log -> new-adventure message.
    assert "start of a new adventure" in recap.recap_from_store(campaign_id, session_id).lower()

    for entry in _entries():
        store.append_log(campaign_id, session_id, entry)

    out = recap.recap_from_store(campaign_id, session_id)
    assert out.startswith("Previously on your adventure...")
    assert "Whispering Caverns" in out
    assert "goblins" in out
    assert "Stealth check" not in out
