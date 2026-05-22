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
