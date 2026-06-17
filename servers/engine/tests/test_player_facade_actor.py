"""S3 multi-agent — the parameterized move facade.

The harness-ensemble model runs each party member (player + N companions, some
ADVERSARIAL) as its OWN ``claude -p`` agent that acts through this SAME constrained
facade. Two env vars retarget it per actor:

  - ``WORLDOS_ACTOR_ID``   — bind to THAT character's sheet (validate its own
    spells/slots/inventory; tag emitted moves with its id).
  - ``WORLDOS_ACTOR_ROLE`` — the role stamped on each move (default "player").

These tests pin the contract that keeps the streams disjoint and the security
boundary intact:
  * an actor validates against ITS OWN sheet (a companion's slots, not the player's);
  * an illegal move (a spell it doesn't know / no slot) is REFUSED;
  * emitted moves carry the right role + actor id;
  * DEFAULT behavior (neither env set) is byte-for-byte today's single-player facade.
"""
import json

import pytest

import player_server as ps
import store
from models import Campaign, Character, Item, SpellSlotLevel


# --- a live on-disk campaign with a player PC AND a companion, each its own sheet ---
def _make_campaign() -> Campaign:
    # Player: a rogue with a Rapier, knows no spells, no slots.
    hero = Character(
        id="char-hero", name="Kield", kind="player",
        inventory=[Item(name="Rapier"), Item(name="Lockpicks")],
        skill_proficiencies=["stealth", "deception"],
    )
    # Companion (the would-be saboteur): a cleric with its OWN spells + a single L1 slot,
    # a healing potion, and a low attitude toward the party (the betrayal hook).
    ally = Character(
        id="char-ally", name="Seraphine", kind="companion",
        spells_known=["Sacred Flame", "Cure Wounds", "Inflict Wounds"],
        spell_slots={1: SpellSlotLevel(maximum=1)},
        inventory=[Item(name="Healing Potion"), Item(name="Mace")],
        skill_proficiencies=["religion", "insight"],
        attitude="wary", attitude_value=-55,
    )
    return Campaign(
        id="camp-actor", title="Ensemble", characters={hero.id: hero, ally.id: ally},
        party=[hero.id, ally.id],
    )


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A persisted campaign + a moves file, with the facade pointed at this state dir.
    Yields a small handle; each test sets the actor env it wants on top."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    moves = tmp_path / "moves.jsonl"
    monkeypatch.setenv("WORLDOS_PLAYER_MOVES", str(moves))
    store.save_campaign(_make_campaign())

    def rows():
        if not moves.exists():
            return []
        return [json.loads(x) for x in moves.read_text(encoding="utf-8").splitlines() if x.strip()]

    return type("Live", (), {"moves": moves, "rows": staticmethod(rows)})


# --- _pc() resolves to the bound ACTOR, not always the player -----------------------
def test_actor_id_binds_to_that_character(live, monkeypatch):
    # No actor env -> the player PC (default behavior).
    assert ps._pc().name == "Kield"
    # Bind the companion -> _pc() now resolves the companion's sheet.
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    assert ps._pc().name == "Seraphine"
    assert ps._actor_id() == "char-ally"


def test_unknown_actor_id_resolves_to_no_sheet(live, monkeypatch):
    # An id that isn't in the live campaign -> no character (moves get refused, not
    # silently mis-bound to the player).
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ghost")
    assert ps._pc() is None
    assert ps.cast_spell("Cure Wounds")["ok"] is False
    assert ps.attack("the player")["ok"] is False


def test_blank_actor_id_is_treated_as_unset(live, monkeypatch):
    # An empty/whitespace env var must NOT select 'no character' — it falls back to
    # the player PC (defensive: a harness that exports an empty var stays safe).
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "   ")
    assert ps._actor_id() == ""
    assert ps._pc().name == "Kield"


# --- a companion validates a CAST against ITS OWN slots, not the player's ------------
def test_companion_cast_validates_against_its_own_sheet(live, monkeypatch):
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    # The companion knows Cure Wounds and has an L1 slot -> allowed.
    assert ps.cast_spell("Cure Wounds")["ok"] is True
    # The PLAYER (rogue) doesn't know it — but we're bound to the COMPANION, so the
    # companion's sheet is what gates the move. Sanity: the player would be refused.
    monkeypatch.delenv("WORLDOS_ACTOR_ID")
    assert ps.cast_spell("Cure Wounds")["ok"] is False  # player doesn't know it


def test_companion_illegal_moves_are_refused(live, monkeypatch):
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    # A spell the companion doesn't know -> refused.
    res = ps.cast_spell("Fireball")
    assert res["ok"] is False and "sheet" in res["error"]
    # An item the companion doesn't carry -> refused (the player's Rapier isn't its item).
    assert ps.use_item("Rapier")["ok"] is False
    # It DOES carry a Healing Potion -> allowed.
    assert ps.use_item("Healing Potion")["ok"] is True
    # Attacking with a weapon it doesn't own -> refused; its own Mace is fine.
    assert ps.attack("Kield", "Rapier")["ok"] is False
    assert ps.attack("Kield", "Mace")["ok"] is True


def test_companion_out_of_slots_is_refused(live, monkeypatch):
    # Spend the companion's only L1 slot, then a leveled spell must be refused (the
    # same C1 hole the player facade closes — a tapped-out caster can't "cast").
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    c = store.load_campaign("camp-actor")
    c.characters["char-ally"].spell_slots[1].used = 1
    store.save_campaign(c)
    res = ps.cast_spell("Cure Wounds")  # leveled, no L1+ slot left
    assert res["ok"] is False and "slot" in res["error"]
    # Sacred Flame is a cantrip (level 0) -> still castable with no slots.
    assert ps.cast_spell("Sacred Flame")["ok"] is True


# --- emitted moves carry the right ROLE + ACTOR ID ----------------------------------
def test_moves_are_tagged_with_role_and_actor_id(live, monkeypatch):
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    monkeypatch.setenv("WORLDOS_ACTOR_ROLE", "companion")
    assert ps.say("'For the party. Of course.'")["ok"] is True
    assert ps.attack("Kield", "Mace")["ok"] is True  # the betrayal, as a LEGAL move
    rows = live.rows()
    assert [m["kind"] for m in rows] == ["say", "attack"]
    assert all(m["role"] == "companion" for m in rows)
    assert all(m["actor_id"] == "char-ally" for m in rows)


def test_attack_move_target_is_recorded(live, monkeypatch):
    # The saboteur's attack is a structured move the engine resolves into real combat —
    # the target rides along so the DM/engine knows WHO is being attacked.
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    monkeypatch.setenv("WORLDOS_ACTOR_ROLE", "companion")
    ps.attack("Kield", "Mace")
    row = live.rows()[-1]
    assert row["target"] == "Kield" and row["weapon"] == "Mace"


# --- my_sheet exposes attitude_value (the betrayal trigger the agent reads) ----------
def test_my_sheet_exposes_attitude_value_for_the_actor(live, monkeypatch):
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    sheet = ps.my_sheet()
    assert sheet["name"] == "Seraphine"
    assert sheet["attitude_value"] == -55          # the companion reads its OWN standing
    assert sheet["attitude"] == "wary"
    assert "cure wounds" in sheet["spells"]
    assert sheet["spell_slots"] == {1: "1/1"}       # remaining/maximum


# --- DEFAULT behavior (no env) is byte-for-byte today's single-player facade ---------
def test_default_role_and_no_actor_id_when_env_unset(live, monkeypatch):
    # Critical: with neither var set, moves are role:"player" with NO actor_id key —
    # exactly what existing duo runs + the dashboard already consume.
    monkeypatch.delenv("WORLDOS_ACTOR_ID", raising=False)
    monkeypatch.delenv("WORLDOS_ACTOR_ROLE", raising=False)
    assert ps._actor_role() == "player"
    assert ps.say("I wait by the door.")["ok"] is True
    row = live.rows()[-1]
    assert row["role"] == "player"
    assert "actor_id" not in row
    # And _pc() still resolves the player PC, validating against the player's sheet.
    assert ps._pc().name == "Kield"
    assert ps.attack("the guard", "Rapier")["ok"] is True   # player's own weapon
    assert ps.attack("the guard", "Mace")["ok"] is False     # not the player's item


def test_default_my_sheet_unchanged_shape_plus_additive_fields(live, monkeypatch):
    # my_sheet gained additive keys (spell_slots/attitude/attitude_value) but the
    # original keys are intact, so existing readers don't break.
    monkeypatch.delenv("WORLDOS_ACTOR_ID", raising=False)
    sheet = ps.my_sheet()
    for k in ("name", "hp", "ac", "skills", "spells", "inventory"):
        assert k in sheet
    assert sheet["name"] == "Kield"
    assert sheet["attitude_value"] == 0  # player's untouched default


# --- A-LOW-1: an actor id may bind ONLY to a live player/companion ------------------
def test_actor_id_pointing_at_a_monster_emits_no_moves(live, monkeypatch):
    # Seed a monster into the live campaign, then point the facade at it. The move
    # palette is the human-play surface — it must NEVER drive a monster/npc, so _pc()
    # resolves to no sheet and every sheet-gated move is refused (same as an unknown
    # actor id: cast/use_item/attack all bail on "no character yet").
    c = store.load_campaign("camp-actor")
    c.characters["mon-ogre"] = Character(id="mon-ogre", name="Ogre", kind="monster",
                                         inventory=[Item(name="Greatclub")])
    store.save_campaign(c)
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "mon-ogre")
    assert ps._pc() is None
    assert ps.attack("Kield", "Greatclub")["ok"] is False
    assert ps.cast_spell("Sacred Flame")["ok"] is False
    assert ps.use_item("Greatclub")["ok"] is False


def test_actor_id_pointing_at_a_dead_character_emits_no_moves(live, monkeypatch):
    # A bound companion that has DIED can no longer act — a corpse drives no moves.
    c = store.load_campaign("camp-actor")
    c.characters["char-ally"].dead = True
    store.save_campaign(c)
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    assert ps._pc() is None
    assert ps.attack("Kield", "Mace")["ok"] is False
    assert ps.cast_spell("Cure Wounds")["ok"] is False


def test_actor_id_for_a_live_companion_still_acts(live, monkeypatch):
    # The guard is narrow: a LIVE player/companion is unaffected (it still emits moves).
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    assert ps._pc().name == "Seraphine"
    assert ps.say("'Ready.'")["ok"] is True


# --- A-LOW-2: WORLDOS_ACTOR_ROLE is clamped to the allowlist -------------------------
def test_actor_role_clamped_to_allowlist(live, monkeypatch):
    # Unknown free text -> "companion" (the safe non-narrator peer role), never trusted
    # verbatim onto the move stream the DM/dashboard read.
    monkeypatch.setenv("WORLDOS_ACTOR_ROLE", "dungeon-master")
    assert ps._actor_role() == "companion"
    monkeypatch.setenv("WORLDOS_ACTOR_ROLE", "")
    assert ps._actor_role() == "player"  # blank -> today's default
    monkeypatch.setenv("WORLDOS_ACTOR_ROLE", "  Companion  ")
    assert ps._actor_role() == "companion"  # trimmed + case-insensitive
    monkeypatch.setenv("WORLDOS_ACTOR_ROLE", "player")
    assert ps._actor_role() == "player"


def test_clamped_role_is_what_lands_on_the_move(live, monkeypatch):
    # The clamp is observable end-to-end: an injected role never reaches the move record.
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    monkeypatch.setenv("WORLDOS_ACTOR_ROLE", "narrator")  # bogus
    ps.say("'Hello.'")
    assert live.rows()[-1]["role"] == "companion"


# --- F12-15 / SYN-07: pin the campaign instead of re-resolving max(updated_at) -------
# The facade re-resolved "the live campaign" as max(updated_at) on EVERY call. With a
# parallel campaign B taking the lead (fresher updated_at), an ACTOR_ID bound to a
# character that only lives in campaign A silently resolved to None — the companion went
# mute / its moves were refused (the #640 silent-switch family). The fix: an additive
# env pin (WORLDOS_CAMPAIGN_ID). Unset -> the heuristic, byte-identical; set -> that
# campaign, regardless of which one is freshest. This is a STATE-INTEGRITY contract:
# a pure facade READ must never flip which campaign is "live" out from under the actor.
def _second_campaign() -> Campaign:
    """A SEPARATE campaign that does NOT contain char-ally — the parallel session that,
    when fresher, would steal the live pointer from camp-actor under the old heuristic."""
    other = Character(id="char-other", name="Brakka", kind="player",
                      inventory=[Item(name="Club")])
    return Campaign(id="camp-other", title="Parallel", characters={other.id: other},
                    party=[other.id])


def test_campaign_id_pins_the_facade_to_that_campaign(live, monkeypatch):
    # Two campaigns: camp-actor (with the companion) and a FRESHER camp-other (without it).
    # Save camp-other LAST so it wins max(updated_at). With ACTOR_ID=char-ally pinned to
    # camp-actor via WORLDOS_CAMPAIGN_ID, the facade resolves the companion's OWN sheet —
    # it does NOT follow the freshest campaign and go mute.
    store.save_campaign(_second_campaign())  # fresher -> would win the heuristic
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    # Sanity: WITHOUT the pin, the heuristic picks the fresher camp-other (which lacks the
    # actor) -> the bound companion silently resolves to no sheet (the bug).
    assert ps._pc() is None
    # WITH the pin, the facade stays on camp-actor and resolves the companion.
    monkeypatch.setenv("WORLDOS_CAMPAIGN_ID", "camp-actor")
    assert ps._campaign().id == "camp-actor"
    assert ps._pc().name == "Seraphine"
    sheet = ps.my_sheet()
    assert sheet["name"] == "Seraphine"


def test_campaign_id_unset_is_byte_identical_heuristic(live, monkeypatch):
    # The pin is additive: unset (or blank/whitespace) -> the original max(updated_at)
    # selector, unchanged. With only camp-actor on disk the facade resolves it either way.
    monkeypatch.delenv("WORLDOS_CAMPAIGN_ID", raising=False)
    assert ps._campaign().id == "camp-actor"
    monkeypatch.setenv("WORLDOS_CAMPAIGN_ID", "   ")  # whitespace == unset
    assert ps._campaign().id == "camp-actor"


def test_campaign_id_unknown_falls_back_to_heuristic(live, monkeypatch):
    # A pin that names a campaign that isn't on disk degrades to the heuristic rather than
    # resolving to None (defensive: a stale/typo'd pin must not silently mute the actor —
    # it falls back to today's behavior, the most-recently-updated campaign).
    monkeypatch.setenv("WORLDOS_CAMPAIGN_ID", "camp-does-not-exist")
    assert ps._campaign().id == "camp-actor"


def test_pure_facade_read_does_not_flip_the_live_campaign(live, monkeypatch):
    # SYN-07 STATE-INTEGRITY: a pure facade READ (my_sheet, _pc, _campaign) must NEVER bump
    # updated_at or re-resolve+flip which campaign is live (the #640/#735 silent-switch family
    # this wave fixes). Capture both campaigns' on-disk state, exercise the read path, and
    # assert nothing on disk moved — the facade is the SOLE non-writer here.
    store.save_campaign(_second_campaign())
    monkeypatch.setenv("WORLDOS_CAMPAIGN_ID", "camp-actor")
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")

    before = {c["id"]: c.get("updated_at") for c in store.list_campaigns()}
    # the full facade read surface
    ps._campaign()
    ps._pc()
    ps.my_sheet()
    after = {c["id"]: c.get("updated_at") for c in store.list_campaigns()}

    assert before == after  # no updated_at bumped -> the live pointer can't have flipped
    # and the freshest campaign is unchanged (the read didn't make camp-actor "win" by writing)
    assert max(after, key=lambda k: after[k] or 0) == max(before, key=lambda k: before[k] or 0)


# --- #928: a companion's wrong-but-intuitive `say` arg shape must NOT be schema-refused ---
# A companion peer-agent called say({"message": ...}) instead of the canonical {"line": ...}.
# FastMCP's auto-generated `sayArguments` model rejected it with
#   `1 validation error for sayArguments / line / Field required [type=missing]`
# BEFORE the function body ran — a hard schema rejection that trips the FATAL
# `no_rejected_tool_calls` behavioral gate and REDs the whole party run. The fix is additive:
# say/do/clarify accept the canonical name OR the aliases message/text, coalescing to the
# first non-blank, so an agent reaching for the natural field name is never refused. These
# tests exercise the REAL rejection surface — the MCP tool manager (which builds + validates
# the pydantic args model), not a direct Python kwargs call — so they would RED pre-fix.
def _call_tool(name: str, args: dict):
    """Invoke a registered MCP tool through the tool manager (the path the peer-agent uses),
    so we validate against the AUTO-GENERATED pydantic args model — the exact layer that
    raised the #928 `Field required` rejection. Returns the tool's result dict."""
    import asyncio

    return asyncio.run(ps.mcp._tool_manager.call_tool(name, args))


def _result_dict(res):
    """The tool manager returns a (content, structured) shape across mcp versions; pull the
    structured ``{"ok": ..., "move": {...}}`` dict the facade actually returns."""
    if isinstance(res, tuple):
        for part in res:
            if isinstance(part, dict):
                return part.get("result", part)
        return res[-1]
    return res


def test_companion_say_with_message_alias_is_not_rejected(live, monkeypatch):
    # THE #928 REPRO: the companion peer-agent's `say` arrived as {"message": ...}. Pre-fix
    # this raised `1 validation error for sayArguments / line / Field required` through the
    # tool manager and tripped the FATAL no_rejected_tool_calls gate. Post-fix it succeeds and
    # records the dialogue verbatim — the move record is byte-identical (kind="say", text=...).
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    monkeypatch.setenv("WORLDOS_ACTOR_ROLE", "companion")
    res = _result_dict(_call_tool("say", {"message": "Seraphine steadies her mace."}))
    assert res["ok"] is True
    row = live.rows()[-1]
    assert row["kind"] == "say"
    assert row["text"] == "Seraphine steadies her mace."
    assert row["role"] == "companion" and row["actor_id"] == "char-ally"


def test_say_text_alias_also_accepted(live, monkeypatch):
    # The other intuitive alias an LLM reaches for: say({"text": ...}).
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    res = _result_dict(_call_tool("say", {"text": "'On your left.'"}))
    assert res["ok"] is True
    assert live.rows()[-1]["text"] == "'On your left.'"


def test_do_and_clarify_message_alias_not_rejected(live, monkeypatch):
    # The same additive tolerance covers the other free-text declares (do/clarify) — the
    # identical schema-rejection class. A wrong-shaped `do`/`clarify` must not RED a run either.
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    do_res = _result_dict(_call_tool("do", {"message": "I move to flank the ogre."}))
    assert do_res["ok"] is True
    assert live.rows()[-1]["text"] == "I move to flank the ogre."
    clr_res = _result_dict(_call_tool("clarify", {"message": "Is the guard armed?"}))
    assert clr_res["ok"] is True
    assert live.rows()[-1]["kind"] == "clarify"
    assert live.rows()[-1]["text"] == "Is the guard armed?"


def test_canonical_line_still_works_and_wins_over_alias(live, monkeypatch):
    # INVARIANT: the canonical `line` is unchanged for the solo/.app + existing duo path, and
    # if both are somehow supplied, `line` wins (deterministic precedence, like server.py's
    # alias coalescing). The solo viewer /move sink is a SEPARATE path and unaffected by this.
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    assert ps.say("'Canonical.'")["ok"] is True       # direct positional, today's call
    assert live.rows()[-1]["text"] == "'Canonical.'"
    res = _result_dict(_call_tool("say", {"line": "'wins'", "message": "'loses'"}))
    assert res["ok"] is True
    assert live.rows()[-1]["text"] == "'wins'"         # canonical takes precedence


def test_empty_say_call_records_blank_line_not_a_crash(live, monkeypatch):
    # All three blank (or omitted) -> coalesces to the canonical "" and records a blank say,
    # same as a positional empty string would. No KeyError / no schema rejection.
    monkeypatch.setenv("WORLDOS_ACTOR_ID", "char-ally")
    res = _result_dict(_call_tool("say", {}))
    assert res["ok"] is True
    assert live.rows()[-1]["text"] == ""
