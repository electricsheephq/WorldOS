import dice


def test_constant():
    r = dice.roll("5")
    assert r.total == 5
    assert r.rolls == []
    assert r.modifier == 5


def test_basic_die_range():
    for _ in range(200):
        r = dice.roll("1d20")
        assert 1 <= r.total <= 20
        assert r.is_d20


def test_modifier():
    r = dice.roll("1d6+3", seed=1)
    assert r.modifier == 3
    assert 4 <= r.total <= 9


def test_negative_modifier():
    r = dice.roll("1d8-1", seed=2)
    assert r.modifier == -1
    assert 0 <= r.total <= 7


def test_advantage_picks_higher():
    r = dice.roll("1d20", advantage=True, seed=42)
    assert r.is_d20
    assert len(r.dropped) == 1
    assert r.total >= r.dropped[0]


def test_disadvantage_picks_lower():
    r = dice.roll("1d20", disadvantage=True, seed=42)
    assert r.total <= r.dropped[0]


def test_advantage_and_disadvantage_cancel():
    r = dice.roll("1d20", advantage=True, disadvantage=True, seed=7)
    assert r.dropped == []  # cancels to a single normal roll


def test_keep_highest():
    r = dice.roll("4d6kh3", seed=3)
    assert len(r.rolls) == 3
    assert len(r.dropped) == 1
    assert min(r.rolls) >= max(r.dropped)


def test_crit_and_fumble_flags():
    saw_crit = saw_fumble = False
    for s in range(500):
        r = dice.roll("1d20", seed=s)
        if r.natural == 20:
            assert r.crit and not r.fumble
            saw_crit = True
        if r.natural == 1:
            assert r.fumble and not r.crit
            saw_fumble = True
    assert saw_crit and saw_fumble


def test_multi_dice_sum():
    r = dice.roll("2d6+1d4+2", seed=10)
    assert 5 <= r.total <= 18


def test_seed_is_reproducible():
    a = dice.roll("3d8+2", seed=99)
    b = dice.roll("3d8+2", seed=99)
    assert a.total == b.total and a.rolls == b.rolls


def test_empty_raises():
    try:
        dice.roll("")
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty expression")


def test_advantage_applies_to_only_first_d20():
    # C1 regression: with two d20 terms, advantage must affect ONLY the first.
    r = dice.roll("1d20+1d20", advantage=True, seed=5)
    assert len(r.dropped) == 1


def test_crit_comes_from_the_test_die_only():
    # C2 regression: a natural 20 on a non-test d20 must NOT count as a crit.
    for s in range(3000):
        r = dice.roll("1d20+1d20", seed=s)
        if r.is_d20 and r.natural != 20 and 20 in r.rolls:
            assert not r.crit
            return
    raise AssertionError("did not find a second-die-20 case to verify")


def test_percentile_shorthand():
    r = dice.roll("d%", seed=1)
    assert 1 <= r.total <= 100


def test_zero_dice_raises():
    try:
        dice.roll("0d6")
    except ValueError:
        return
    raise AssertionError("expected ValueError for 0d6")


def test_keep_more_than_rolled_raises():
    try:
        dice.roll("2d6kh3")
    except ValueError:
        return
    raise AssertionError("expected ValueError when keeping more dice than rolled")


def test_roll_rejects_pathological_dice():
    # A pathological count/sides must raise (not hang allocating a giant list) — the
    # `roll` MCP tool is publicly reachable, so this guards against a DoS.
    for expr in ("100000000d20", "1d100000000"):
        try:
            dice.roll(expr)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {expr!r}")


def test_roll_rejects_pathologically_long_expression():
    expr = "+".join(["1d6"] * 2000)
    try:
        dice.roll(expr)
    except ValueError:
        return
    raise AssertionError("expected ValueError for pathologically long expression")


def test_legit_rolls_unaffected_by_bounds():
    # Real D&D rolls stay well under the bounds and are unchanged.
    assert dice.roll("20d6+5", seed=1).total > 0          # high-level fireball
    assert 1 <= dice.roll("1d100", seed=1).total <= 100   # percentile die
    assert 1 <= dice.roll("1d20", seed=1).total <= 20
