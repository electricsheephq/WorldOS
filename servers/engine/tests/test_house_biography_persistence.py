"""Loop-10 #383: regression test for house + biography persistence across the
create_character -> Character model -> snapshot -> /character-surface chain.

PR #369 wired both fields into the Creation wizard's bindHero spec. The engine
seating path used to silently drop them at four sites -- this test asserts they
now flow end-to-end:

  1. create_character() accepts house= + biography= kwargs
  2. Character model stores both fields
  3. The persisted snapshot carries them on the PC record
  4. (NOT tested here -- viewer-side) /character-surface surfaces them as
     hero.house + hero.biography for screen-character.jsx to render
"""

from __future__ import annotations

import server


def _new_campaign() -> str:
    """Return a fresh campaign id ready for create_character calls.

    Matches the pattern in test_action_economy.py / test_adversarial_release.py
    (server.create_campaign(name)["id"]), which is what other server-level
    tests use to get a campaign_id without going through the seed_campaign
    content-loader path.
    """
    return server.create_campaign("House-Biography Persistence")["id"]


def test_create_character_accepts_house_and_biography_kwargs():
    """Sanity: the create_character signature actually carries the new kwargs.

    Calling with the kwargs must not TypeError. This guards the screen-create ->
    bindHero -> startProviderSession -> play.sh -> create_character chain at its
    engine endpoint.
    """
    camp = _new_campaign()
    out = server.create_character(
        camp,
        name="Aubree Test",
        kind="player",
        race="human",
        class_name="ranger",
        abilities={"strength": 12, "dexterity": 14, "constitution": 12,
                   "intelligence": 10, "wisdom": 13, "charisma": 11},
        background="folk-hero",
        apply_srd_defaults=True,
        house="Three Bells",
        biography="Once carried a king's letter to a place that does not exist anymore.",
    )
    assert isinstance(out, dict) and "id" in out


def test_house_and_biography_persist_on_the_character_model():
    """The model must carry both fields after creation, and they must survive
    the save_campaign round-trip implicit in create_character.
    """
    camp = _new_campaign()
    rec = server.create_character(
        camp,
        name="Aubree Anvil",
        kind="player",
        race="dwarf",
        class_name="fighter",
        abilities={"strength": 16, "dexterity": 12, "constitution": 15,
                   "intelligence": 10, "wisdom": 11, "charisma": 8},
        background="folk-hero",
        apply_srd_defaults=True,
        house="Anvilforge",
        biography="Three winters in the Iron Shield; one summer at the Spear Gate.",
    )
    pc = server.get_character(camp, rec["id"])
    assert pc["house"] == "Anvilforge"
    assert pc["biography"].startswith("Three winters in the Iron Shield")


def test_house_and_biography_default_to_empty_when_omitted():
    """Existing call sites (NPC spawn, monster spawn, companion seat) MUST keep
    working -- the new kwargs default to "" and the snapshot must NOT carry a
    None/null for either field on a character whose creator didn't supply them.
    Guards the additive-only contract.
    """
    camp = _new_campaign()
    rec = server.create_character(
        camp,
        name="Stoic Stranger",
        kind="npc",
    )
    npc = server.get_character(camp, rec["id"])
    assert npc["house"] == ""
    assert npc["biography"] == ""


def test_house_and_biography_round_trip_through_get_character():
    """End-to-end: explicit values written, snapshot serialized to a dict
    (get_character returns the projected record), and the fields are present +
    intact. This is the contract the viewer's /character-surface depends on.
    """
    camp = _new_campaign()
    rec = server.create_character(
        camp,
        name="Karlach Ember",
        kind="player",
        race="tiefling",
        class_name="barbarian",
        abilities={"strength": 17, "dexterity": 14, "constitution": 16,
                   "intelligence": 8, "wisdom": 10, "charisma": 12},
        background="outlander",
        apply_srd_defaults=True,
        house="Ember (foundling -- no kin recorded)",
        biography=(
            "Born into the Avernus engine-shops with the Hellfire still in her chest. "
            "The infernal contract was cut; the heart is hers again. The forge owes her a name."
        ),
    )
    pc = server.get_character(camp, rec["id"])
    # House + biography survived the model -> snapshot -> dict path
    assert "Ember" in pc["house"]
    assert "Hellfire" in pc["biography"]
    # Sanity: pre-existing identity fields still work alongside the new ones
    assert pc["race"] == "tiefling"
    assert pc["background"] == "outlander"
