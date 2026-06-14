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


# ── SYN-08 / F07-5 / F14-16 (issue #805): recap is BYTE-capped, not just count- ──
# capped. recap.py bounded COUNT (12 entries) but not SIZE — 12 x ~4KB beats
# reproduced 48,631B live every cold open. The fix adds a per-entry sentence-
# boundary char cap (~400) + a total byte budget (~6KB), defaulted, trimming
# OLDEST-first so the newest beats stay intact (recency is what the gates read).
# Short entries stay byte-identical (the existing tests above guard that).
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F07-5, F14-16, SYN-08).

def test_format_recap_total_byte_budget():
    """A run of fat beats is capped to ~max_chars total — the whole-recap budget,
    not just per-entry — and stays well under the unbounded 48KB it produced."""
    entries = [
        SessionLogEntry(t=float(i), kind="narration", text=("X" * 4000) + f" beat {i}.")
        for i in range(12)
    ]
    out = recap.format_recap(entries)
    assert len(out) <= 6500  # ~6KB budget + the intro slack
    # The unbounded join would be ~48KB; this is an order of magnitude smaller.
    assert len(out) < 12000


def test_format_recap_keeps_newest_when_budget_trims():
    """Oldest-first trimming: when the TOTAL budget bites, the NEWEST beats survive
    and the oldest drop (recency is the story memory the gates read). Driven with a
    tight total budget so the trim mechanism is exercised directly (the per-entry
    cap alone keeps the default config well under 6KB)."""
    entries = [
        SessionLogEntry(t=float(i), kind="narration", text=f"BEAT{i}END is the whole beat.")
        for i in range(12)
    ]
    out = recap.format_recap(entries, max_chars=80)
    # newest beats present; oldest trimmed out of the tight budget
    assert "BEAT11END" in out
    assert "BEAT0END" not in out
    assert len(out) <= 80 + len(recap._INTRO) + 4


def test_format_recap_per_entry_char_cap_at_sentence_boundary():
    """A single very long beat is truncated to ~max_entry_chars, preferring a
    sentence boundary so the recap reads as prose, not a mid-word cut."""
    long_text = (
        "The party crossed the bridge. " * 5  # ~150 chars of whole sentences
        + "Then they walked on and on and on " * 40  # pushes well past the cap
    )
    out = recap.format_recap([SessionLogEntry(t=1.0, kind="narration", text=long_text)])
    body = out[len(recap._INTRO) + 1:]
    assert len(body) <= 420  # ~400 cap + a little boundary slack
    assert "The party crossed the bridge." in out  # opening sentence preserved


def test_format_recap_short_entries_byte_identical():
    """The caps NEVER touch short entries — the common case stays byte-for-byte
    today's output (additive-by-default, recency preserved)."""
    short = [
        SessionLogEntry(t=1.0, kind="narration", text="The torch sputters."),
        SessionLogEntry(t=2.0, kind="dialogue", text="This way.", speaker="Lyra"),
    ]
    # Reproduce the legacy join by hand to prove no truncation crept in.
    expected = (
        recap._INTRO + " The torch sputters. " + 'Lyra said, "This way."'
    )
    assert recap.format_recap(short) == expected


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
