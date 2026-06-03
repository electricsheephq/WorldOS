"""Character skill-name case normalization (QA 2026-06-03 — optimizer crit).

A canon-character record (or a DM `patch={"skills":["Arcana",...]}` alias) can introduce skill
names that are Capitalized or space-separated. Skill names are compared case-sensitively everywhere
(Character.skill_bonus, social_check, the viewer Skills tab) against the lowercase-underscore
SKILL_ABILITIES keys, so an un-normalized capitalized list silently matches nothing → "0 proficient"
and skill checks that miss the proficiency bonus (a L5 Wizard/Sage showed Arcana +3 instead of +6).
Character._normalize_skill_case normalizes at the model boundary; load_canon_character runs it via
model_validate, so the seat path + saved snapshot end up correct.
"""
import server  # noqa: F401 — importing the engine resolves Character's forward refs (model_rebuild)
from models import Character, SKILL_ABILITIES


def test_skill_names_normalized_to_lowercase_underscore():
    ch = Character.model_validate({
        "id": "rolan", "name": "Rolan",
        "classes": [{"name": "Wizard", "level": 5}],
        "abilities": {"intelligence": 17},
        "proficiency_bonus": 3,
        "skill_proficiencies": ["Arcana", "History", "Animal Handling"],
        "skill_expertise": ["Sleight Of Hand"],
    })
    assert ch.skill_proficiencies == ["arcana", "history", "animal_handling"]
    assert ch.skill_expertise == ["sleight_of_hand"]


def test_skill_bonus_finds_proficiency_after_normalization():
    """The whole point: skill_bonus must add the proficiency bonus for a (formerly capitalized)
    proficient skill — Arcana = INT mod + proficiency_bonus, not the bare INT mod."""
    ch = Character.model_validate({
        "id": "rolan", "name": "Rolan",
        "classes": [{"name": "Wizard", "level": 5}],
        "abilities": {"intelligence": 17},  # +3
        "proficiency_bonus": 3,
        "skill_proficiencies": ["Arcana"],
    })
    int_mod = ch.ability_modifier(SKILL_ABILITIES["arcana"])
    assert ch.skill_bonus("arcana") == int_mod + 3
    # a non-proficient skill stays at the bare ability modifier
    assert ch.skill_bonus("nature") == ch.ability_modifier(SKILL_ABILITIES["nature"])
