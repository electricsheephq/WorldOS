import pytest

import combat
import npc as npc_mod
import server
from models import Character


def test_shift_attitude():
    assert npc_mod.shift_attitude("indifferent", 1) == "friendly"
    assert npc_mod.shift_attitude("indifferent", -1) == "wary"
    assert npc_mod.shift_attitude("hostile", -1) == "hostile"  # floored
    assert npc_mod.shift_attitude("helpful", 1) == "helpful"  # capped
    assert npc_mod.shift_attitude("guarded", 1) == "indifferent"  # guarded -> wary -> +1
    assert npc_mod.shift_attitude("guarded", -1) == "hostile"  # wary -> -1
    assert npc_mod.shift_attitude("", 1) == "friendly"  # blank -> indifferent -> +1


# --- F10-6: the two attitude tracks reconcile (value <-> band) ----------------------
def test_normalize_maps_unfriendly_to_wary():
    # F10-6(c): "unfriendly" (the 3.5e/PF diplomacy word a DM reaches for) was unmapped
    # and silently collapsed to "indifferent" — a whole band too friendly. It now lands
    # on the engine's mirror band, "wary".
    assert npc_mod.normalize("unfriendly") == "wary"
    assert npc_mod.normalize("Unfriendly") == "wary"  # case-insensitive like the rest


def test_band_for_value_maps_the_numeric_scale_onto_the_track():
    # F10-6(a): there was NO value<->band mapping anywhere, so a -10 value could sit next
    # to a "wary" label with nothing reconciling them. band_for_value is that bridge: the
    # -100..+100 scale projected onto the five-step track, symmetric around 0.
    assert npc_mod.band_for_value(-100) == "hostile"
    assert npc_mod.band_for_value(-60) == "hostile"
    assert npc_mod.band_for_value(-40) == "wary"
    assert npc_mod.band_for_value(-20) == "wary"
    assert npc_mod.band_for_value(0) == "indifferent"
    assert npc_mod.band_for_value(19) == "indifferent"
    assert npc_mod.band_for_value(20) == "friendly"
    assert npc_mod.band_for_value(59) == "friendly"
    assert npc_mod.band_for_value(60) == "helpful"
    assert npc_mod.band_for_value(100) == "helpful"


def test_band_for_value_is_symmetric():
    # the split is mirror-symmetric: band_for_value(+v) is the track-reflection of
    # band_for_value(-v), so a "friendly" +40 mirrors a "wary" -40.
    track = npc_mod.ATTITUDE_TRACK
    for v in (20, 40, 60, 80, 100):
        pos = track.index(npc_mod.band_for_value(v))
        neg = track.index(npc_mod.band_for_value(-v))
        assert pos + neg == len(track) - 1


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("Social")["id"]


def test_set_attitude_and_memory(campaign):
    npc_id = server.create_character(campaign, "Brakka", kind="npc")["id"]
    server.set_attitude(campaign, npc_id, "guarded")
    server.remember(campaign, npc_id, "The party bought a round.")
    sheet = server.get_character(campaign, npc_id)
    assert sheet["attitude"] == "guarded"
    assert "The party bought a round." in sheet["memory"]
    server.forget(campaign, npc_id, "The party bought a round.")
    assert "The party bought a round." not in server.get_character(campaign, npc_id)["memory"]


def test_forget_unknown_raises(campaign):
    npc_id = server.create_character(campaign, "NPC", kind="npc")["id"]
    with pytest.raises(Exception):
        server.forget(campaign, npc_id, "never said this")


def test_social_check_success_and_failure(campaign):
    pc = server.create_character(campaign, "Bard", kind="player", abilities={"charisma": 16})["id"]
    npc_id = server.create_character(campaign, "Guard", kind="npc")["id"]

    server.set_attitude(campaign, npc_id, "indifferent")
    out = server.social_check(campaign, pc, npc_id, "persuasion", dc=1)  # always succeeds
    assert out["success"] is True and out["new_attitude"] == "friendly"

    server.set_attitude(campaign, npc_id, "indifferent")
    out2 = server.social_check(campaign, pc, npc_id, "persuasion", dc=100)  # always fails
    assert out2["success"] is False and out2["new_attitude"] == "wary"
    assert out["kind"] == "influence" and "read" not in out  # influence is tagged, no read block


def test_social_check_read_skills_perceive_without_shifting_attitude(campaign):
    # A READ (insight/perception/investigation) tells the actor something; it must
    # NEVER change how the NPC feels — reading or MISreading someone is observer
    # clarity, not influence. (A failed Insight wrongly souring a warmth-first
    # antagonist was the QA-flagged bug this fixes.)
    pc = server.create_character(campaign, "Watcher", kind="player", abilities={"wisdom": 16})["id"]
    npc_id = server.create_character(campaign, "Stranger", kind="npc")["id"]

    server.set_attitude(campaign, npc_id, "indifferent")
    ok = server.social_check(campaign, pc, npc_id, "insight", dc=1)  # always succeeds
    assert ok["kind"] == "read" and ok["success"] is True
    assert ok["old_attitude"] == "indifferent" and ok["new_attitude"] == "indifferent"
    assert ok["read"]["perceived_attitude"] == "indifferent"  # a clear read reveals the stance

    miss = server.social_check(campaign, pc, npc_id, "insight", dc=100)  # always fails
    assert miss["kind"] == "read" and miss["success"] is False
    assert miss["new_attitude"] == "indifferent"  # a flubbed read is NOT an attitude penalty
    assert miss["read"]["perceived_attitude"] is None  # uncertain read, nothing asserted as truth

    # perception and investigation are reads too — attitude holds either way
    for sk in ("perception", "investigation"):
        out = server.social_check(campaign, pc, npc_id, sk, dc=100)
        assert out["kind"] == "read" and out["new_attitude"] == "indifferent"


def test_social_check_influence_raises_and_lowers_attitude_value(campaign):
    # Feature 1: an INFLUENCE check nudges the numeric per-NPC relationship in
    # ADDITION to the free-text track — +15 on a success, -10 on a failure, clamped.
    pc = server.create_character(campaign, "Bard", kind="player", abilities={"charisma": 16})["id"]
    npc_id = server.create_character(campaign, "Guard", kind="npc")["id"]

    # value starts at the neutral default
    assert server.get_character(campaign, npc_id)["attitude_value"] == 0

    win = server.social_check(campaign, pc, npc_id, "persuasion", dc=1)  # always succeeds
    assert win["old_attitude_value"] == 0 and win["new_attitude_value"] == 15
    assert server.get_character(campaign, npc_id)["attitude_value"] == 15

    loss = server.social_check(campaign, pc, npc_id, "persuasion", dc=100)  # always fails
    assert loss["old_attitude_value"] == 15 and loss["new_attitude_value"] == 5
    assert server.get_character(campaign, npc_id)["attitude_value"] == 5


def test_social_check_read_leaves_attitude_value_untouched(campaign):
    # Feature 1 + the read-vs-influence invariant: an Insight READ must NOT move the
    # numeric value any more than it moves the free-text track — observer clarity is
    # not influence. (Mirrors the read-skills attitude test, for the number.)
    pc = server.create_character(campaign, "Watcher", kind="player", abilities={"wisdom": 16})["id"]
    npc_id = server.create_character(campaign, "Stranger", kind="npc")["id"]
    server.set_attitude(campaign, npc_id, "indifferent", value=22)

    ok = server.social_check(campaign, pc, npc_id, "insight", dc=1)  # clear read
    assert ok["kind"] == "read"
    assert ok["old_attitude_value"] == 22 and ok["new_attitude_value"] == 22

    miss = server.social_check(campaign, pc, npc_id, "insight", dc=100)  # flubbed read
    assert miss["new_attitude_value"] == 22  # a missed read is not a numeric penalty
    assert server.get_character(campaign, npc_id)["attitude_value"] == 22


def test_social_check_attitude_value_clamps_at_bounds(campaign):
    # Repeated successes can't push the value past +100; repeated failures can't drop
    # it below -100.
    pc = server.create_character(campaign, "Diplomat", kind="player", abilities={"charisma": 18})["id"]
    npc_id = server.create_character(campaign, "Noble", kind="npc")["id"]

    server.set_attitude(campaign, npc_id, "friendly", value=95)
    out = server.social_check(campaign, pc, npc_id, "persuasion", dc=1)  # +15 would be 110
    assert out["new_attitude_value"] == 100  # clamped at the ceiling

    server.set_attitude(campaign, npc_id, "wary", value=-95)
    out2 = server.social_check(campaign, pc, npc_id, "persuasion", dc=100)  # -10 would be -105
    assert out2["new_attitude_value"] == -100  # clamped at the floor


def test_set_attitude_value_and_default(campaign):
    # Feature 1: set_attitude(value=...) sets the number; omitting value leaves it.
    npc_id = server.create_character(campaign, "Innkeep", kind="npc")["id"]
    assert server.get_character(campaign, npc_id)["attitude_value"] == 0  # default neutral

    out = server.set_attitude(campaign, npc_id, "friendly", value=40)
    assert out["attitude"] == "friendly" and out["attitude_value"] == 40

    # free-text-only call must NOT reset the number it didn't touch
    out2 = server.set_attitude(campaign, npc_id, "guarded")
    assert out2["attitude"] == "guarded" and out2["attitude_value"] == 40

    # value is clamped to the -100..+100 scale
    assert server.set_attitude(campaign, npc_id, "devoted", value=999)["attitude_value"] == 100
    assert server.set_attitude(campaign, npc_id, "hostile", value=-999)["attitude_value"] == -100


def test_set_attitude_value_only_does_not_wipe_the_label(campaign):
    # F10-6(b): set_attitude had `attitude: str = ""` + an UNCONDITIONAL `ch.attitude = attitude`,
    # so a value-only call (set_attitude(value=...)) blew the free-text label away to "". A DM
    # nudging just the number must NOT erase the disposition word the bar reads alongside it.
    npc_id = server.create_character(campaign, "Sentry", kind="npc")["id"]
    server.set_attitude(campaign, npc_id, "guarded", value=10)
    assert server.get_character(campaign, npc_id)["attitude"] == "guarded"

    # value-only call (no `attitude`): the label must survive, only the number moves.
    out = server.set_attitude(campaign, npc_id, value=-30)
    assert out["attitude"] == "guarded"  # NOT wiped to ""
    assert out["attitude_value"] == -30
    assert server.get_character(campaign, npc_id)["attitude"] == "guarded"

    # an explicit label still overwrites (the documented use is unchanged).
    out2 = server.set_attitude(campaign, npc_id, "friendly")
    assert out2["attitude"] == "friendly"


def test_adjust_attitude_nudges_and_clamps(campaign):
    # Feature 1: adjust_attitude nudges the number by a delta, clamped, free-text intact.
    npc_id = server.create_character(campaign, "Merchant", kind="npc")["id"]
    server.set_attitude(campaign, npc_id, "wary", value=10)

    up = server.adjust_attitude(campaign, npc_id, 25)
    assert up["old_attitude_value"] == 10 and up["attitude_value"] == 35
    assert server.get_character(campaign, npc_id)["attitude"] == "wary"  # text unchanged

    down = server.adjust_attitude(campaign, npc_id, -50)
    assert down["attitude_value"] == -15

    assert server.adjust_attitude(campaign, npc_id, 1000)["attitude_value"] == 100  # ceiling
    assert server.adjust_attitude(campaign, npc_id, -1000)["attitude_value"] == -100  # floor


def test_social_check_unknown_skill_raises(campaign):
    pc = server.create_character(campaign, "PC", kind="player")["id"]
    npc_id = server.create_character(campaign, "NPC", kind="npc")["id"]
    with pytest.raises(Exception):
        server.social_check(campaign, pc, npc_id, "flossing", dc=10)


def test_social_check_self_or_pc_target_raises(campaign):
    pc = server.create_character(campaign, "PC", kind="player", abilities={"charisma": 14})["id"]
    with pytest.raises(Exception):
        server.social_check(campaign, pc, pc, "persuasion", dc=10)  # actor == target
    pc2 = server.create_character(campaign, "PC2", kind="player")["id"]
    with pytest.raises(Exception):
        server.social_check(campaign, pc, pc2, "persuasion", dc=10)  # target is a PC


def test_remember_dedupes(campaign):
    npc_id = server.create_character(campaign, "NPC", kind="npc")["id"]
    server.remember(campaign, npc_id, "owes the party")
    out = server.remember(campaign, npc_id, "owes the party")  # duplicate
    assert out["memory"].count("owes the party") == 1


def test_forget_case_insensitive(campaign):
    npc_id = server.create_character(campaign, "NPC", kind="npc")["id"]
    server.remember(campaign, npc_id, "The Party Helped")
    server.forget(campaign, npc_id, "the party helped")  # different case
    assert server.get_character(campaign, npc_id)["memory"] == []


def test_npc_met_flag_tracks_first_contact(campaign):
    # Owner live-QA: Relationships listed seeded roster NPCs the party had NEVER met
    # ("he apparently already knows the 8 NPCs"). `met` is the engine-level truth the
    # dashboard filters on — a roster NPC EXISTS unmet; encountering them flips it.
    pc = server.create_character(campaign, "Hero", kind="player",
                                 abilities={"charisma": 14, "wisdom": 14})["id"]
    stranger = server.create_character(campaign, "Stranger", kind="npc")["id"]
    assert server.get_character(campaign, stranger)["met"] is False  # exists, not yet encountered

    # A READ (insight) is first contact too — you can't read someone you haven't met.
    server.social_check(campaign, pc, stranger, "insight", dc=1)
    assert server.get_character(campaign, stranger)["met"] is True

    # An influence check against another NPC flips met as well.
    guard = server.create_character(campaign, "Guard", kind="npc")["id"]
    server.social_check(campaign, pc, guard, "persuasion", dc=1)
    assert server.get_character(campaign, guard)["met"] is True

    # Recruiting a roster NPC into the party means they're met.
    ally = server.create_character(campaign, "Ally", kind="npc")["id"]
    server.recruit_companion(campaign, ally, class_name="fighter", level=1)
    assert server.get_character(campaign, ally)["met"] is True


def test_scene_extra_social_check_marks_nobody(campaign):
    # The ephemeral target path (npc_id="" + target_name) must persist NOTHING — no roster
    # NPC created, so a one-off social beat can't pollute Relationships with a stray stranger.
    pc = server.create_character(campaign, "Hero", kind="player", abilities={"charisma": 14})["id"]
    before = len(server._require(campaign).characters)
    out = server.social_check(campaign, pc, "", "persuasion", dc=1, target_name="a fishmonger")
    assert out.get("ephemeral") is True
    assert len(server._require(campaign).characters) == before  # nobody added


def test_recruit_companion_clears_dead_state_from_stub_killed_in_combat(campaign):
    # Regression (live-QA): a recruited companion was stuck dead=true. Trace: an identity
    # STUB (e.g. load_canon_character) starts at the placeholder max_hp=1, so the FIRST hit
    # in combat trips the SRD massive-damage instant-death rule (damage >= max_hp) and flags
    # it dead. recruit_companion then filled a real sheet but NEVER cleared ch.dead — leaving
    # an "alive" companion who couldn't act and for whom long_rest raised "cannot rest while
    # dead". Recruiting with living HP must clear the death state.
    npc_id = server.create_character(campaign, "Bram", kind="npc")["id"]

    # Force the dead=true state through the engine's OWN combat death path (faithful to the
    # bug): a max_hp=1 stub takes a hit and dies via the massive-damage rule.
    c = server._require(campaign)
    ch = c.characters[npc_id]
    ch.max_hp = 1
    ch.current_hp = 1
    combat.apply_damage(ch, 4)  # 4 >= max_hp 1 -> instant death
    server.save_campaign(c)
    assert server.get_character(campaign, npc_id)["dead"] is True  # the bug's precondition

    # Recruit with a real sheet (the clean promote-to-companion path).
    server.recruit_companion(campaign, npc_id, class_name="fighter", level=3, max_hp=28)

    sheet = server.get_character(campaign, npc_id)
    assert sheet["dead"] is False  # a recruited, living-HP companion is NOT dead
    assert sheet["stable"] is False
    assert sheet["current_hp"] > 0
    assert sheet["death_saves"]["failures"] == 0 and sheet["death_saves"]["successes"] == 0
    # Condition.UNCONSCIOUS must not linger from the dying state.
    assert "unconscious" not in sheet["conditions"]

    # The payoff: a subsequent long_rest now SUCCEEDS (previously raised "cannot rest while dead").
    rest = server.long_rest(campaign, npc_id)
    assert rest["hp"] == f"{sheet['max_hp']}/{sheet['max_hp']}"  # rested to full, no exception
    assert server.get_character(campaign, npc_id)["current_hp"] == sheet["max_hp"]


def test_load_canon_stub_is_not_a_one_hit_kill(campaign):
    # Regression: the identity-stub Character default is max_hp=1 — an INSTANT-KILL combatant
    # (one hit trips the massive-damage rule before the stub is ever fleshed out). A freshly
    # built identity stub must take a swing without dying. Exercised on a plain stub-shaped
    # Character (no world-ingested canon roster needed in this fixture's empty campaign).
    stub = Character(name="Freshly Loaded", kind="npc")  # model default: max_hp=1
    # The fix gives load_canon_character's stub a sane floor (>=10); mirror that here and prove
    # the floor survives a normal hit.
    stub.max_hp = max(stub.max_hp, 10)
    stub.current_hp = stub.max_hp
    out = combat.apply_damage(stub, 4)  # a routine hit
    assert stub.dead is False and out["dead"] is False  # survives — not one-shot
