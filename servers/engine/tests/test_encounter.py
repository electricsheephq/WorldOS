import pytest

import encounter


# --- CR -> XP ---
@pytest.mark.parametrize(
    "cr,xp",
    [
        ("0", 10),
        ("1/8", 25),
        ("1/4", 50),
        ("1/2", 100),
        ("1", 200),
        ("5", 1800),
        ("10", 5900),
        ("20", 25000),
        ("30", 155000),
    ],
)
def test_xp_for_cr_strings(cr, xp):
    assert encounter.xp_for_cr(cr) == xp


@pytest.mark.parametrize(
    "cr,xp",
    [
        (0, 10),
        (0.125, 25),
        (0.25, 50),
        (0.5, 100),
        (1, 200),
        (5, 1800),
        (5.0, 1800),
        (20, 25000),
    ],
)
def test_xp_for_cr_numbers(cr, xp):
    assert encounter.xp_for_cr(cr) == xp


def test_xp_for_cr_whitespace_tolerant():
    assert encounter.xp_for_cr(" 1/4 ") == 50


def test_xp_for_cr_unknown_raises():
    with pytest.raises(ValueError):
        encounter.xp_for_cr("31")
    with pytest.raises(ValueError):
        encounter.xp_for_cr(2.5)


# --- party XP thresholds ---
def test_thresholds_single_l1():
    assert encounter.xp_thresholds([1]) == {
        "easy": 25, "medium": 50, "hard": 75, "deadly": 100,
    }


def test_thresholds_four_l1_pcs():
    # SRD: 4 x level-1 thresholds.
    assert encounter.xp_thresholds([1, 1, 1, 1]) == {
        "easy": 100, "medium": 200, "hard": 300, "deadly": 400,
    }


def test_thresholds_mixed_levels_sum():
    # L1 (25/50/75/100) + L5 (250/500/750/1100)
    assert encounter.xp_thresholds([1, 5]) == {
        "easy": 275, "medium": 550, "hard": 825, "deadly": 1200,
    }


def test_thresholds_clamped_to_table_range():
    # Level 25 clamps to the level-20 row.
    assert encounter.xp_thresholds([25]) == encounter.xp_thresholds([20])


# --- encounter multiplier tiers ---
@pytest.mark.parametrize(
    "n,mult",
    [
        (1, 1.0),
        (2, 1.5),
        (3, 2.0),
        (6, 2.0),
        (7, 2.5),
        (10, 2.5),
        (11, 3.0),
        (14, 3.0),
        (15, 4.0),
        (30, 4.0),
    ],
)
def test_encounter_multiplier_tiers(n, mult):
    assert encounter.encounter_multiplier(n) == mult


def test_adjusted_xp_applies_multiplier():
    # 4 goblins @ 50 XP -> 200 base * x2 (3-6 monsters) = 400 adjusted.
    assert encounter.adjusted_xp([50, 50, 50, 50]) == 400


# --- encounter difficulty classification ---
def test_four_l1_pcs_vs_four_goblins_is_deadly():
    # Canonical SRD example: 4x L1 PCs (deadly threshold = 400) vs 4 goblins.
    # 4 * 50 XP = 200 base, x2 multiplier = 400 adjusted == deadly threshold.
    party = [1, 1, 1, 1]
    goblins = [encounter.xp_for_cr("1/4")] * 4
    assert encounter.adjusted_xp(goblins) == 400
    assert encounter.encounter_difficulty(party, goblins) == "deadly"


@pytest.mark.parametrize(
    # 4x L1 budget: easy=100, medium=200, hard=300, deadly=400.
    "monster_xps,expected",
    [
        ([], "trivial"),                   # no monsters -> 0 adjusted
        ([10], "trivial"),                 # 10 * 1 = 10 < 100 easy
        ([50], "trivial"),                 # one CR1/4: 50 * 1 = 50 < 100 easy
        ([50, 50], "easy"),                # 100 * 1.5 = 150 -> >=100 easy, <200 medium
        ([50, 50, 50], "hard"),            # 150 * 2 = 300 == hard threshold
        ([50, 50, 50, 50], "deadly"),      # 200 * 2 = 400 == deadly threshold
    ],
)
def test_difficulty_bands_four_l1(monster_xps, expected):
    party = [1, 1, 1, 1]
    assert encounter.encounter_difficulty(party, monster_xps) == expected


def test_difficulty_trivial_below_easy():
    # One bandit (25 XP) vs 4x L1 (easy=100): 25 * 1 = 25 -> trivial.
    assert encounter.encounter_difficulty([1, 1, 1, 1], [25]) == "trivial"


def test_difficulty_easy_at_boundary():
    # 2 bandits @ 25 XP = 50 base, x1.5 = 75 -> still trivial (<100 easy).
    # 4 bandits @ 25 = 100 base, x2 = 200 adjusted -> medium for 4x L1.
    assert encounter.encounter_difficulty([1, 1, 1, 1], [25, 25, 25, 25]) == "medium"
