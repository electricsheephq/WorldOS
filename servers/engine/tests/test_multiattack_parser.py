"""F01-1 (#771): the Multiattack parser must not overcount 'replace one attack'
riders or ', or it makes' alternatives.

The naive parser split the desc on `and`/`,` and summed every numbered
"attack(s)" clause, so 13/344 bestiary creatures were granted phantom extra
attacks (~+50% DPR) — and the bad count was ENFORCED (attack()'s per-action
ceiling) and INSTRUCTED ("Run N attack call(s)"), not merely displayed.

Two SRD wording families were misread:
  - substitution riders: "It can replace one attack with a Bite attack." (+1)
  - alternatives: "..., or it makes two Hurl Flame attacks." (both branches summed)

The fix pre-filters the desc to its COUNTING clause (drop replace/instead-of
sentences; keep the FIRST ", or it/the-X makes" alternative) — and must NOT
split on bare " or ", which appears INSIDE counting clauses ("two Javelin or
Morningstar attacks" — Bugbear Stalker).
"""

import pytest

# The 13 overcounted creatures from the audit's full-344 sweep (skeptic-verified),
# with their RAW-correct Multiattack counts. Pulled from the LIVE bestiary so the
# test guards the real data path end-to-end.
OVERCOUNTED = [
    ("Absolute Soul Seer", 2),  # main parsed 4: both alternatives summed
    ("Barbed Devil", 2),        # main parsed 4: both alternatives summed
    ("Clay Golem", 2),          # main parsed 5: "or three Slam if Hasten" summed
    ("Medusa", 3),              # main parsed 6: both alternatives summed
    ("Cloud Giant", 2),         # main parsed 3: "replace one attack" rider +1
    ("Horned Devil", 3),        # main parsed 4: "replace one attack" rider +1
    ("Infernal Inquisitor", 3), # main parsed 4: "replace one attack" rider +1
    ("Wight", 2),               # main parsed 3: "replace one attack" rider +1
    ("Werebear", 2),            # main parsed 3: "replace one attack with a Bite" +1
    ("Wereboar", 2),
    ("Wererat", 2),
    ("Weretiger", 2),
    ("Werewolf", 2),
]

# Creatures whose standard wordings must parse EXACTLY as before (the corrected
# pre-filter changed only the 13 above in the full-344 sweep). Includes the two
# bare-" or "-inside-a-counting-clause traps and the accidentally-correct dragons.
UNCHANGED_CONTROLS = [
    ("Adult Gold Dragon", 3),  # "It can replace one attack…" sentence has no comma — was already 3
    ("Marilith", 6),           # "makes six Pact Blade attacks and uses Constrict"
    ("Bugbear Stalker", 2),    # "two Javelin or Morningstar attacks" — bare ' or ' INSIDE the clause
    ("Assassin", 3),           # "using Shortsword or Light Crossbow in any combination"
    ("Aboleth", 2),            # "two Tentacle attacks and uses either…"
    ("Bandit Captain", 2),     # "two attacks, using Scimitar and Pistol in any combination"
]


def _live_desc(name: str) -> str:
    import bestiary

    sb = bestiary.stat_block(name)
    assert sb is not None, f"{name} missing from the bestiary"
    ma = next((a for a in sb.get("actions", []) if a["name"].lower() == "multiattack"), None)
    assert ma is not None, f"{name} has no Multiattack action"
    return ma["desc"]


@pytest.mark.parametrize("name,expected", OVERCOUNTED)
def test_overcounted_creatures_corrected(name, expected):
    """Each of the 13 overcounted creatures parses to its RAW-correct count (#771)."""
    import server

    assert server._parse_multiattack_count(_live_desc(name)) == expected


@pytest.mark.parametrize("name,expected", UNCHANGED_CONTROLS)
def test_standard_wordings_unchanged(name, expected):
    """Standard wordings (incl. bare-' or ' inside counting clauses) stay byte-identical."""
    import server

    assert server._parse_multiattack_count(_live_desc(name)) == expected


@pytest.mark.parametrize(
    "desc,expected",
    [
        # rider sentence dropped (substitution, not a third attack)
        (
            "The werewolf makes two attacks, using Scratch or Longbow in any combination. "
            "It can replace one attack with a Bite attack.",
            2,
        ),
        # ", or it makes" alternative — first branch only
        (
            "The devil makes one Claws attack and one Tail attack, "
            "or it makes two Hurl Flame attacks.",
            2,
        ),
        ("The golem makes two Slam attacks, or it makes three Slam attacks if it used Hasten this turn.", 2),
        (
            "The medusa makes two Claw attacks and one Snake Hair attack, "
            "or it makes three Poison Ray attacks.",
            3,
        ),
        # bare " or " inside a counting clause must NOT be split
        ("The bugbear makes two Javelin or Morningstar attacks.", 2),
        # "instead of" rider variant
        ("The creature makes two Slam attacks. It can use Stomp instead of one Slam attack.", 2),
        # plain wordings (regression guard for the pre-filter)
        ("The captain makes two attacks, using Scimitar and Pistol in any combination.", 2),
        ("makes one Ram attack, one Bite attack, and one Claw attack", 3),
    ],
)
def test_counting_clause_wordings(desc, expected):
    """Pure-string parser table over the rider/alternative/trap wordings (F01-1)."""
    import server

    assert server._parse_multiattack_count(desc) == expected


def test_medusa_composition_first_alternative_only():
    """The composition must come from the FIRST alternative — not a merged 6-attack
    sequence spanning both branches (the fully-resolving wrong instruction the audit
    flagged: Medusa surfaced a coherent-looking 6-attack sequence; real allowance 3)."""
    import server

    desc = (
        "The medusa makes two Claw attacks and one Snake Hair attack, "
        "or it makes three Poison Ray attacks."
    )
    assert server._parse_multiattack_composition(desc) == ["Claw", "Claw", "Snake Hair"]


def test_replace_rider_excluded_from_composition():
    """A 'replace one attack' rider must not inject a phantom attack name."""
    import server

    desc = (
        "The wight makes two attacks, using Necrotic Sword or Necrotic Bow in any "
        "combination. It can replace one attack with a use of Life Drain."
    )
    # "two attacks" has no attack NAME between the count and 'attacks' — composition
    # degrades to [] (count-only surfacing), and crucially contains no Life Drain entry.
    assert server._parse_multiattack_composition(desc) == []


def test_werewolf_third_attack_rejected(tmp_path, monkeypatch):
    """Integration (F01-1): the Werewolf's Multiattack is TWO attacks — the engine
    must reject the third attack in one action. On main the parser counted 3 and
    attack() permitted AND instructed all three (live probe: attacks [1,2,3] resolved)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Werewolf F01-1")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=60)["id"]
    wid = server.spawn_monster(cid, "Werewolf")["spawned"][0]["id"]
    server.start_combat(cid, [pc, wid])
    # Initiative order is random; if the PC won, pass its turn to reach the werewolf.
    if server.get_state(cid)["current_turn"] == pc:
        server.use_action(cid, pc, "skip")
        server.next_turn(cid)
    assert server.get_state(cid)["current_turn"] == wid

    a1 = server.attack(cid, wid, pc, attack_bonus=4, damage_dice="1d8")
    assert a1["attacks_allowed_this_turn"] == 2, (
        f"Werewolf Multiattack allowance must be 2; got {a1['attacks_allowed_this_turn']}"
    )
    server.attack(cid, wid, pc, attack_bonus=4, damage_dice="1d8")
    with pytest.raises(ValueError, match="Multiattack grants 2 attack"):
        server.attack(cid, wid, pc, attack_bonus=4, damage_dice="1d8")
