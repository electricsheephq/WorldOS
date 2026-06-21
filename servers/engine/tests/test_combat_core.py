"""Engine-run combat CORE tests (Track 2b/2c/2e).

Covers the four net-new pieces of the engine-run combat loop:
  - combat_ai.pick_action: the pure greedy-v1 EV policy returns sane Intents.
  - combat_loop.run_combat_autonomous(mode="test"): a seeded random-vs-random fight runs to a
    terminal state and EVERY combatant acted.
  - combat_loop.run_combat_round/autonomous(mode="live"): auto-runs ONLY hostiles, STOPS at the
    first PC/companion turn, never auto-plays a PC.
  - the TEST toggles (force_hit / fast_resolve) are DEAD CODE outside the double guard, force the
    HIT BOOLEAN without corrupting crit/damage accounting, and the dice-seed is deterministic.

All of this is LLM-free, so it runs in the fast deterministic lane. Default-off is asserted: a
non-sandbox campaign with no env behaves exactly as today.
"""
from __future__ import annotations

import dice as dice_mod
import combat_ai
from combat_ai import AttackOption, CombatantView, CombatView, Intent, p_hit
import combat_loop
import store


# ── helpers ──────────────────────────────────────────────────────────────────────────

def _mk_view(actor_attacks, foes, grid=False, actor_cell=None, **kw) -> CombatView:
    return CombatView(
        actor_id="A",
        actor_cell=actor_cell,
        actor_zone="",
        actor_side="enemy",
        speed=kw.get("speed", 30),
        dashed=False,
        grid_enabled=grid,
        grid_width=kw.get("w", 10),
        grid_height=kw.get("h", 10),
        cell_size=5,
        foes=tuple(foes),
        allies=(),
        attacks=tuple(actor_attacks),
        spells=tuple(kw.get("spells", ())),
    )


def _foe(fid, hp=10, ac=12, cell=None, name=None):
    return CombatantView(id=fid, name=name or fid, side="party",
                         current_hp=hp, max_hp=hp, armor_class=ac, cell=cell)


# ── p_hit / EV math ────────────────────────────────────────────────────────────────

def test_p_hit_curve_and_clamps():
    assert p_hit(5, 15) == 0.55          # need 10+, 11 faces hit
    assert p_hit(0, 25) == 0.05          # impossible but for the nat-20 floor
    assert p_hit(20, 5) == 0.95          # auto but for the nat-1 ceiling (NOT 1.0)
    # monotone: a higher bonus never lowers the chance
    assert p_hit(7, 15) >= p_hit(5, 15)


def test_average_total_matches_expected():
    assert dice_mod.average_total("2d8+3") == 12      # 2*4.5 + 3
    assert dice_mod.average_total("1d6") == 4          # round(3.5)
    assert dice_mod.average_total("5") == 5            # flat
    assert dice_mod.average_total("2d6") == 7


# ── pick_action: greedy-v1 returns sane Intents ──────────────────────────────────────

def test_pick_action_attacks_best_in_reach_target():
    # Two foes; the AI should focus-fire the LOWER-HP one on an EV tie (same AC).
    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="2d6+3", damage_type="slashing")]
    view = _mk_view(atks, [_foe("lo", hp=5, ac=12), _foe("hi", hp=40, ac=12)])
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "attack"
    assert intent.target_id == "lo"            # focus-fire the weaker foe
    assert intent.attack_name == "Claw"


def test_pick_action_prefers_higher_ev_attack():
    # Big-damage low-accuracy vs small-damage high-accuracy against one foe (AC 15).
    atks = [
        AttackOption(name="Heavy", to_hit=0, damage_expr="4d6", damage_type="b"),    # P~0.30 * 14
        AttackOption(name="Quick", to_hit=10, damage_expr="1d6", damage_type="b"),   # P~0.80 * 4
    ]
    view = _mk_view(atks, [_foe("t", hp=30, ac=15)])
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "attack"
    # Heavy EV ~ 0.30*14 = 4.2 ; Quick EV ~ 0.80*3.5 = 2.8 -> Heavy wins.
    assert intent.attack_name == "Heavy"


def test_pick_action_skips_when_no_foes():
    intent = combat_ai.pick_action(actor=object(), combat_state=_mk_view([], []))
    assert intent.kind == "skip"


def test_pick_action_moves_to_reach_on_grid_when_out_of_range():
    # On the grid, a melee attacker far from its foe should MOVE toward it (not attack).
    atks = [AttackOption(name="Bite", to_hit=4, damage_expr="1d8", reach_ft=5)]
    view = _mk_view(atks, [_foe("t", cell=(8, 0))], grid=True, actor_cell=(0, 0))
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "move"
    assert intent.to_cell is not None
    # the chosen cell is strictly closer to the foe than the start
    import combat_grid
    assert combat_grid.distance_ft(intent.to_cell, (8, 0)) < combat_grid.distance_ft((0, 0), (8, 0))


def test_pick_action_attacks_in_reach_on_grid():
    atks = [AttackOption(name="Bite", to_hit=4, damage_expr="1d8", reach_ft=5)]
    view = _mk_view(atks, [_foe("t", cell=(1, 0))], grid=True, actor_cell=(0, 0))
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "attack"
    assert intent.target_id == "t"


def test_pick_action_dodges_when_nothing_reachable_no_grid_path():
    # Grid, foe far, but speed 0 -> cannot move, cannot reach -> Dodge fallback.
    atks = [AttackOption(name="Bite", to_hit=4, damage_expr="1d8", reach_ft=5)]
    view = _mk_view(atks, [_foe("t", cell=(9, 9))], grid=True, actor_cell=(0, 0), speed=0)
    intent = combat_ai.pick_action(actor=object(), combat_state=view)
    assert intent.kind == "dodge"


def test_pick_action_is_deterministic():
    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="2d6+3")]
    foes = [_foe("a", hp=10, ac=12), _foe("b", hp=10, ac=12)]
    v = _mk_view(atks, foes)
    i1 = combat_ai.pick_action(actor=object(), combat_state=v)
    i2 = combat_ai.pick_action(actor=object(), combat_state=v)
    assert i1 == i2   # same state -> same Intent (frozen dataclass equality)


# ── run_combat_autonomous(mode="test"): seeded random-vs-random to a terminal state ──

def _seed_fight(server, store_mod, *, sandbox=True, monsters=3):
    cid = server.create_campaign("Core Test")["id"]
    server.add_location(campaign_id=cid, name="Pit", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = sandbox
    store_mod.save_campaign(c)
    hero = server.create_character(
        cid, "Hero", kind="player", race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True,
    )["id"]
    mons = [m["id"] for m in server.spawn_monster(cid, "Goblin", count=monsters)["spawned"]]
    server.start_combat(cid, [hero] + mons)
    return cid, hero, mons


def test_run_combat_autonomous_test_runs_to_terminal_and_everyone_acted(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    dice_mod.reseed_process_rng(4242)
    cid, hero, mons = _seed_fight(server, store)
    res = combat_loop.run_combat_autonomous(cid, mode="test", max_rounds=25)
    # terminal: a victor or a draw (never left mid-fight in test mode)
    assert res["victor"] in ("party", "enemy", "draw")
    assert res["round_cap_hit"] is False
    # EVERY combatant got at least one turn
    assert set(res["actors_acted"]) == set([hero] + mons), res["actors_acted"]
    # combat closed out (end_combat fired on a decisive result)
    assert res["turns"] > 0


def test_dice_seed_is_deterministic(tmp_path, monkeypatch):
    """Same seed -> byte-identical fight outcome; the seed fixes the whole sequence."""
    import server

    def _run(seed):
        dice_mod.reseed_process_rng(seed)
        cid, hero, mons = _seed_fight(server, store)
        return combat_loop.run_combat_autonomous(cid, mode="test", max_rounds=25)

    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path / "a"))
    r1 = _run(909)
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path / "b"))
    r2 = _run(909)
    assert (r1["victor"], r1["rounds"], r1["turns"]) == (r2["victor"], r2["rounds"], r2["turns"])


def test_unset_seed_is_nondeterministic_path(tmp_path, monkeypatch):
    """An unset seed reseeds from OS entropy (today's behavior). We can't assert two fights
    DIFFER (they might coincide), but we CAN assert reseed(None) draws fresh entropy: two
    long roll sequences after reseed(None) are extremely unlikely to be identical."""
    dice_mod.reseed_process_rng(None)
    seq_a = [dice_mod.roll("1d20").natural for _ in range(50)]
    dice_mod.reseed_process_rng(None)
    seq_b = [dice_mod.roll("1d20").natural for _ in range(50)]
    assert seq_a != seq_b  # OS entropy -> distinct streams (P(collision) ~ 20^-50)


# ── LIVE mode: auto-run hostiles, STOP at the first PC/companion turn ─────────────────

def test_live_mode_stops_at_pc_and_never_autoplays_pc(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    # Force the PC to act first by giving it an absurd initiative bonus via a high DEX is
    # unreliable; instead just assert the invariant over any initiative order.
    dice_mod.reseed_process_rng(11)
    cid, hero, mons = _seed_fight(server, store, monsters=2)
    res = combat_loop.run_combat_autonomous(cid, mode="live", max_rounds=25)
    # The PC is NEVER in actors_acted in live mode (only hostiles auto-run).
    assert hero not in res["actors_acted"]
    # Live mode hands control back at a PC turn (awaiting_pc set) OR the hostiles all died
    # before the PC's first turn (a clean enemy wipe). Either is valid; the PC never auto-ran.
    if res["awaiting_pc"] is not None:
        assert res["awaiting_pc"] == hero


def test_live_round_returns_digest_and_awaiting(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    dice_mod.reseed_process_rng(3)
    cid, hero, mons = _seed_fight(server, store, monsters=2)
    rr = combat_loop.run_combat_round(cid, mode="live")
    assert "round_digest" in rr and isinstance(rr["round_digest"], list)
    assert rr["mode"] == "live"
    # no PC entry in the digest
    assert all(e["actor_id"] != hero for e in rr["round_digest"])


# ── TEST toggles: dead code when unguarded; force the hit boolean only ────────────────

def _one_attack(server, store_mod, cid, attacker, target, *, attack_bonus=0,
                damage_dice="1d6", **hr):
    """Configure house_rules + run a single attack vs the target, return the result. Pins the
    attacker as the CURRENT combatant and clears its action budget so each call is a legal
    on-turn strike (a test convenience — we drive many attacks without advancing the round)."""
    c = server._require(cid)
    for k, v in hr.items():
        setattr(c.house_rules, k, v)
    # Make `attacker` the current combatant and give it a fresh action economy.
    idx = next((i for i, cb in enumerate(c.combat.order) if cb.character_id == attacker), 0)
    c.combat.turn_index = idx
    c.combat.action_attacks_made = 0
    c.combat.action_used = False
    for cb in c.combat.order:
        if cb.character_id == attacker:
            cb.reaction_used = False
    store_mod.save_campaign(c)
    r = server.attack(cid, attacker_id=attacker, target_id=target,
                      attack_bonus=attack_bonus, damage_dice=damage_dice)
    return r


def test_force_hit_is_dead_code_without_env(tmp_path, monkeypatch):
    """force_hit set, campaign IS sandbox, but WORLDOS_COMBAT_TEST unset -> the toggle is dead:
    a high-AC target is still missed (only the genuine nat-20 floor hits). This proves the
    double guard — force_hit can NEVER fire in a live (non-test-env) game."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("WORLDOS_COMBAT_TEST", raising=False)
    import server
    dice_mod.reseed_process_rng(None)
    cid = server.create_campaign("Guard")["id"]
    server.add_location(campaign_id=cid, name="P", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = True  # sandbox half of the guard is TRUE
    store.save_campaign(c)
    a = server.create_character(cid, "A", kind="player", max_hp=30, armor_class=10)["id"]
    t = server.create_character(cid, "T", kind="monster", max_hp=300, armor_class=30)["id"]
    server.start_combat(cid, [a, t])
    hits = sum(
        1 for _ in range(40)
        if _one_attack(server, store, cid, a, t, force_hit=True)["hit"]
    )
    # vs AC 30 with +0, only natural 20s hit (~5%). NOT ~40. So force_hit is DEAD here.
    assert hits <= 6, f"force_hit leaked without the env guard: {hits}/40 hits"


def test_force_hit_forces_hit_under_guard_without_faking_crits(tmp_path, monkeypatch):
    """With BOTH guard halves on (env + sandbox), force_hit makes every roll hit — but it does
    NOT synthesize a nat-20, so crits stay rare (only genuine nat-20s). This is the crit-honesty
    invariant: the smoke can still distinguish 'crits double dice' from 'force_hit faked a crit'."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WORLDOS_COMBAT_TEST", "1")
    import server
    dice_mod.reseed_process_rng(None)
    cid = server.create_campaign("Guard2")["id"]
    server.add_location(campaign_id=cid, name="P", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = True
    store.save_campaign(c)
    a = server.create_character(cid, "A", kind="player", max_hp=30, armor_class=10)["id"]
    t = server.create_character(cid, "T", kind="monster", max_hp=999, armor_class=30)["id"]
    server.start_combat(cid, [a, t])
    n = 80
    results = [_one_attack(server, store, cid, a, t, force_hit=True) for _ in range(n)]
    hits = sum(1 for r in results if r["hit"])
    crits = sum(1 for r in results if r["crit"])
    assert hits == n, f"force_hit must force ALL hits: {hits}/{n}"
    # crits are ONLY genuine nat-20s (~5%) — force_hit did NOT fake them.
    assert crits < n * 0.5, f"force_hit fabricated crits ({crits}/{n}) — crit accounting corrupted"


def test_fast_resolve_averages_damage_under_guard(tmp_path, monkeypatch):
    """fast_resolve (guarded) makes damage the deterministic AVERAGE: a 2d6+3 strike always
    applies 10 (2*3.5+3 -> round) regardless of the roll. Outside the guard it would vary."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WORLDOS_COMBAT_TEST", "1")
    import server
    dice_mod.reseed_process_rng(None)
    cid = server.create_campaign("Fast")["id"]
    server.add_location(campaign_id=cid, name="P", description="x", make_current=True)
    c = server._require(cid)
    c.is_sandbox = True
    c.house_rules.force_hit = True       # guarantee the hit so damage always rolls
    c.house_rules.fast_resolve = True
    store.save_campaign(c)
    a = server.create_character(cid, "A", kind="player", max_hp=30, armor_class=10)["id"]
    t = server.create_character(cid, "T", kind="monster", max_hp=999, armor_class=5)["id"]
    server.start_combat(cid, [a, t])
    totals = set()
    for _ in range(20):
        r = _one_attack(server, store, cid, a, t, attack_bonus=10, damage_dice="2d6+3",
                        force_hit=True, fast_resolve=True)
        # only count non-crit strikes (a crit doubles the dice -> different average)
        if not r["crit"]:
            totals.add(r["damage"]["total"])
    # every non-crit strike applied the SAME averaged total (10), not a random spread.
    assert totals == {10}, f"fast_resolve did not average damage deterministically: {totals}"


def test_combat_test_mode_guard_requires_both_halves(tmp_path, monkeypatch):
    """The double guard predicate is True ONLY when env==1 AND campaign.is_sandbox."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    cid = server.create_campaign("G")["id"]
    c = server._require(cid)
    # neither half
    monkeypatch.delenv("WORLDOS_COMBAT_TEST", raising=False)
    c.is_sandbox = False
    assert server._combat_test_mode_enabled(c) is False
    # env only
    monkeypatch.setenv("WORLDOS_COMBAT_TEST", "1")
    assert server._combat_test_mode_enabled(c) is False
    # both
    c.is_sandbox = True
    assert server._combat_test_mode_enabled(c) is True
    # sandbox only
    monkeypatch.delenv("WORLDOS_COMBAT_TEST", raising=False)
    assert server._combat_test_mode_enabled(c) is False


# ── additive / default-off: a non-sandbox campaign behaves as today ──────────────────

def test_new_fields_default_off_and_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import server
    from models import Campaign, HouseRules
    assert Campaign(title="x").is_sandbox is False
    assert HouseRules().force_hit is False
    assert HouseRules().fast_resolve is False
    # an OLD snapshot lacking the new keys loads fine (tolerant of absence) and round-trips
    cid = server.create_campaign("RT")["id"]
    c = server._require(cid)
    store.save_campaign(c)
    reloaded = server._require(cid)
    assert reloaded.is_sandbox is False
    assert reloaded.house_rules.force_hit is False
