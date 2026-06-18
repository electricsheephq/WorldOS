"""Unit tests for qa/distill.py — the _audit_fields helper that surfaces engine-auto-fired
mechanics the 240-char tool_result preview would otherwise truncate (so the Angry-DM scorer
can audit them tool-sourced, not only in DM prose).

Stdlib + pytest only. Self-contained.

Run with the engine venv (which has pytest):
    uv run --directory servers/engine python -m pytest qa/test_distill.py -p no:cacheprovider
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# distill lives next to this test (qa/); make it importable regardless of pytest rootdir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import distill  # noqa: E402


def test_audit_fields_surfaces_repeat_saves_with_roll_detail():
    # A next_turn return carrying two Hold Person end-of-turn escape saves (#209): one held,
    # one that frees the target. The exact roll values must survive (the scorer flagged them
    # "irrecoverable from the truncated next_turn output").
    res = json.dumps(
        {
            "round": 2,
            "repeat_saves": [
                {"character_id": "char_cap", "name": "Hold Person", "ability": "wis",
                 "dc": 14, "roll": 5, "natural": 5, "success": False, "ended": False},
                {"character_id": "char_cap", "name": "Hold Person", "ability": "wis",
                 "dc": 14, "roll": 18, "natural": 18, "success": True, "ended": True,
                 "cleared_condition": "paralyzed"},
            ],
        }
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 2
    assert "Hold Person on char_cap" in lines[0]
    assert "WIS 5 (nat 5) vs DC 14 → held" in lines[0]
    assert "WIS 18 (nat 18) vs DC 14 → ENDS" in lines[1]


def test_audit_fields_surfaces_maneuver_damage():
    # An attack/use_resource return carrying the Battle Master superiority die (#213).
    res = json.dumps(
        {"ok": True, "maneuver_damage": {"maneuver": "Trip Attack", "die": "1d8",
                                         "rolled": 6, "applied": True}}
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "maneuver-damage: Trip Attack 1d8=6 applied=True" in lines[0]


def test_audit_fields_surfaces_crit_doubled_maneuver_damage():
    # On a crit the superiority die DOUBLES (#213/A) — the distill must show the doubling
    # was applied (CRIT×2 + the base/extra split) so the Angry-DM lens reads it tool-sourced.
    res = json.dumps(
        {"ok": True, "maneuver_damage": {"maneuver": "Trip Attack", "die": "1d8",
                                         "rolled": 12, "applied": True, "crit_doubled": True,
                                         "base_rolled": 6, "crit_extra": 6}}
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "maneuver-damage: Trip Attack 1d8=12 CRIT×2 (6+6) applied=True" in lines[0]


def test_audit_fields_surfaces_concentration_save():
    # The #792 auto-concentration roll sits after target_state in the result JSON, so the
    # 240-char preview truncates it and the Angry-DM lens mis-scores a tool-sourced save as a
    # hallucinated "10 vs 10" prose number. Surfacing it closes the highest-risk false defect.
    res = json.dumps(
        {"ok": True, "concentration_save": {
            "target": "Maren", "rolled": True, "ability": "con",
            "dc": 10, "roll": 13, "natural": 11, "maintained": True, "spell": "Hold Person"}}
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "concentration-save: Maren CON 13 (nat 11) vs DC 10 → MAINTAINED (Hold Person)" in lines[0]
    # A broken save renders BROKEN.
    broken = json.dumps({"concentration_save": {
        "target": "Maren", "ability": "con", "dc": 14, "roll": 8, "maintained": False}})
    assert "→ BROKEN" in distill._audit_fields(broken)[0]


def test_audit_fields_surfaces_freed_targets_and_advantage_consumed():
    # Held victims released when concentration breaks (#792/F3-6), and a consumed on-hit
    # advantage rider (Guiding Bolt) — both engine-auto-fired, both truncated by the preview.
    freed = distill._audit_fields(json.dumps({"freed_targets": ["goblin-1", "goblin-2"]}))
    assert len(freed) == 1 and "freed-on-concentration-end: goblin-1, goblin-2" in freed[0]
    adv = distill._audit_fields(json.dumps({"advantage_consumed": "Guiding Bolt"}))
    assert len(adv) == 1 and "advantage-consumed: Guiding Bolt" in adv[0]
    # Empty/absent → no-op.
    assert distill._audit_fields(json.dumps({"freed_targets": [], "advantage_consumed": None})) == []
def test_audit_fields_surfaces_multiattack_budget():
    # An attack() return for a Multiattack monster (the Ghoul: 2 Bites) carries
    # attacks_made_this_turn / attacks_allowed_this_turn — but the 240-char preview
    # truncates them (the attack result is ~585 chars), so the scorer cannot SEE the
    # engine constrain the monster to its Multiattack budget. csmed-4 ("the Ghoul
    # conjured a two-Claw Multiattack that doesn't exist") is this blindness: the
    # ceiling IS enforced, but its tool-sourcing never reaches the distilled transcript.
    res = json.dumps(
        {
            "attacker": "Ghoul",
            "target": "Hero",
            "attack_roll": {"total": 21, "natural": 17, "detail": "1d20[17] +4 = 21"},
            "hit": True,
            "attacks_made_this_turn": 1,
            "attacks_allowed_this_turn": 2,
            "multiattack_grants": 2,  # engine-surfaced: the budget IS a stat-block Multiattack
        }
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "attack-budget: Ghoul 1/2 attacks this turn (Multiattack)" in lines[0]


def test_audit_fields_labels_pc_extra_attack_budget_not_multiattack():
    # A PC's multi-strike turn comes from Extra Attack / Action Surge, NOT Multiattack —
    # the engine omits multiattack_grants, so distill must label it "(Extra Attack)" and
    # never miscredit a PC with a monster Multiattack. The engine surfaces extra_attacks
    # so the source is tool-sourced (a genuine Extra-Attack fighter: extra_attacks=1).
    res = json.dumps(
        {
            "attacker": "Fighter",
            "target": "Orc",
            "hit": True,
            "attacks_made_this_turn": 1,
            "attacks_allowed_this_turn": 2,
            "extra_attacks": 1,
            "surge_actions": 0,
        }
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "attack-budget: Fighter 1/2 attacks this turn (Extra Attack)" in lines[0]


def test_audit_fields_labels_action_surge_budget_not_extra_attack():
    # cs-timing F-2: Aldric (Fighter L4, extra_attacks=0) spent Action Surge for a 2nd swing.
    # The budget came from Action Surge, NOT the Extra Attack feature (which is L5). distill
    # must read "(Action Surge)" — and a character with extra_attacks:0 must NEVER get an
    # "Extra Attack" annotation (the finding's CI assert).
    res = json.dumps(
        {
            "attacker": "Aldric",
            "target": "Ghoul",
            "hit": True,
            "attacks_made_this_turn": 2,
            "attacks_allowed_this_turn": 2,
            "extra_attacks": 0,
            "surge_actions": 1,
        }
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "attack-budget: Aldric 2/2 attacks this turn (Action Surge)" in lines[0]
    assert "Extra Attack" not in lines[0]  # extra_attacks:0 => never mislabeled the feature


def test_audit_fields_labels_extra_attack_plus_action_surge():
    # A high-level fighter with BOTH Extra Attack (extra_attacks=1) AND a spent Action Surge
    # reads both sources, in order.
    res = json.dumps(
        {
            "attacker": "Bron",
            "target": "Ogre",
            "hit": True,
            "attacks_made_this_turn": 1,
            "attacks_allowed_this_turn": 4,
            "extra_attacks": 1,
            "surge_actions": 1,
        }
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "attack-budget: Bron 1/4 attacks this turn (Extra Attack + Action Surge)" in lines[0]


def test_audit_fields_suppresses_single_attack_budget():
    # A plain single-attack swing (PC with no Extra Attack: allowed=1) must NOT surface a
    # budget line — no noise for the 95% of attacks that are one strike (additive discipline,
    # mirrors maneuver_damage/repeat_saves only firing when there is something to audit).
    res = json.dumps(
        {
            "attacker": "Hero",
            "target": "Goblin",
            "hit": True,
            "attacks_made_this_turn": 1,
            "attacks_allowed_this_turn": 1,
        }
    )
    assert distill._audit_fields(res) == []


def test_audit_fields_surfaces_rejected_multiattack_overflow_plain_string():
    # The REJECTED 3rd attack (a ValueError) reaches the transcript as a PLAIN-STRING
    # is_error tool_result (the MCP layer renders the exception text, not JSON). distill
    # must surface it so the scorer sees the engine REFUSE the phantom attack — the exact
    # csmed-4 evidence ("a Multiattack that doesn't exist" was the DM narrating past a
    # ceiling the engine had already enforced).
    res = (
        "Ghoul cannot attack: this creature's Multiattack grants 2 attack(s) "
        "per turn; 2 already made this turn."
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "attack-rejected: this creature's Multiattack grants 2 attack(s) per turn" in lines[0]


def test_audit_fields_surfaces_rejected_multiattack_overflow_json_error():
    # Same rejection when the MCP layer wraps it as {"error": "..."} JSON.
    res = json.dumps(
        {
            "error": "Ghoul cannot attack: this creature's Multiattack grants 2 attack(s) "
            "per turn; 2 already made this turn."
        }
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "attack-rejected: this creature's Multiattack grants 2 attack(s) per turn" in lines[0]


def test_audit_fields_is_safe_on_non_json_non_dict_and_irrelevant():
    assert distill._audit_fields("not json at all") == []
    assert distill._audit_fields(json.dumps([1, 2, 3])) == []
    assert distill._audit_fields(json.dumps({"unrelated": "result"})) == []
    # An empty/null repeat_saves is a no-op (the common case: most turns fire none).
    assert distill._audit_fields(json.dumps({"repeat_saves": []})) == []
    assert distill._audit_fields(json.dumps({"repeat_saves": None})) == []


def test_audit_fields_tolerates_partial_repeat_save_entries():
    # A malformed entry (missing keys, or not a dict) must not crash — it degrades to "?".
    res = json.dumps({"repeat_saves": ["bogus", {"name": "Hold Monster"}]})
    lines = distill._audit_fields(res)
    assert len(lines) == 1  # the string entry is skipped; the partial dict still renders
    assert "Hold Monster" in lines[0]
