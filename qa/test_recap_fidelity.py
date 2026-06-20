#!/usr/bin/env python3
"""Recap content-fidelity guard — wires qa/fact_fidelity.py to a REAL lossy consumer.

`recap.format_recap` (the DM's "Previously on…" / `previously_on`, read every resume) is LOSSY by
design and latency-pressured: it keeps only the most-recent `max_entries` story beats, soft-truncates
each to `max_entry_chars`, and drops OLDEST-first under a `max_chars` byte budget. Those budgets are
tunable knobs under active pressure to lean further for latency — and nothing guards them against
silently dropping a CONTINUITY-CRITICAL fact (an antagonist, the central MacGuffin, the frame), the
exact loss the 1–5 lens is blind to.

This is that guard: the recap of a reference session must preserve its critical facts at the shipped
defaults, and the test has TEETH — a leaned budget drops a critical fact and fact-fidelity catches it
(if recap weren't lossy, or fact-fidelity couldn't detect a drop, the differential assertion fails).

Run (single-process):
    uv run --directory servers/engine python -m pytest qa/test_recap_fidelity.py -q -p no:xdist
"""
from __future__ import annotations

import sys
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import fact_fidelity as ff  # noqa: E402
from recap import format_recap  # noqa: E402  (engine module; on path under servers/engine)
from models import SessionLogEntry  # noqa: E402


def _beat(i: int, kind: str, text: str, speaker: str | None = None) -> SessionLogEntry:
    return SessionLogEntry(t=float(i), kind=kind, text=text, speaker=speaker)


# A reference session whose continuity-critical facts span the arc: the FRAME reveal is EARLY
# (beat 2), the antagonist + MacGuffin are LATE. recap is recency-biased, so leaning max_entries
# drops the early critical fact while keeping the late ones — a realistic latency-lean regression.
def _reference_session() -> list[SessionLogEntry]:
    return [
        _beat(1, "narration", "Greyharbor docks, a cold grey morning. Sera Vey arrives chasing a stolen courier packet."),
        _beat(2, "narration", "The seal on the recovered packet is her own Wardens mark. She is not the investigator — she is the frame."),
        _beat(3, "dialogue", "You're holding something three parties want. We are the one not planning to take it.", speaker="Dob"),
        _beat(4, "narration", "Wrapped beside the cipher is a Silverwatch officer's seal, the kind only a records clerk would hold."),
        _beat(5, "narration", "One name sits in the clear where the code should be: Lady Ashryn."),
        _beat(6, "dialogue", "The warehouse door opens and Veyl Marrow steps through — the architect who built the road.", speaker="Tomas"),
        _beat(7, "narration", "The packet hid the Ledger of Thirty-One names — every Reach survivor and the Wardens who sheltered them."),
        _beat(8, "narration", "Sera takes the ledger. The road to the Spindle is open."),
    ]


# The continuity-critical facts the recap must carry forward. critical = the spine the next session
# cannot resume without.
_CRITICAL_FACTS = [
    ff.Fact(id="the_frame", desc="Sera is the frame, not the investigator", patterns=[r"the frame"], severity="critical"),
    ff.Fact(id="antagonist_veyl", desc="the architect Veyl Marrow", patterns=[r"Veyl Marrow"], severity="critical"),
    ff.Fact(id="macguffin_thirty_one", desc="the Ledger of Thirty-One names", patterns=[r"Thirty-One"], severity="critical"),
    ff.Fact(id="silverwatch_seal", desc="the Silverwatch officer's seal", patterns=[r"Silverwatch"], severity="high"),
]


def test_recap_preserves_critical_facts_at_shipped_defaults():
    recap = format_recap(_reference_session())
    report = ff.score_fidelity(_CRITICAL_FACTS, recap)
    assert not report.critical_loss, f"recap dropped critical fact(s) at defaults: {report.missing}"
    assert report.fidelity == 1.0, report.missing


def test_leaning_recap_budget_drops_a_critical_fact_and_fidelity_catches_it():
    log = _reference_session()
    full = ff.score_fidelity(_CRITICAL_FACTS, format_recap(log))
    # simulate a latency-lean of the recap knob (keep only the most-recent 3 beats)
    leaned = ff.score_fidelity(_CRITICAL_FACTS, format_recap(log, max_entries=3))
    # the lean silently drops the EARLY 'frame' reveal — a continuity-critical fact the full recap kept
    assert not full.critical_loss
    assert leaned.critical_loss, "leaning the recap should drop a critical fact"
    assert "the_frame" in leaned.missing
    assert leaned.fidelity < full.fidelity
