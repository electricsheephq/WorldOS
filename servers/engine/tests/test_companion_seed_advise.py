"""F06-1 + F06-3 — companion operational-state seeding parity + advise enrichment.

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F6-1, F6-3).

F06-1: TWO of the THREE companion-creation paths seed NO arc and NO dossier, so the
arc machine (camp_scene / check_companion_arc / agendas) is structurally inert on the
DOMINANT path (`create_character`, 111 calls vs 38 recruit) — 20/20 live snapshot
companions had arc=None/dossier=None. The fix routes ALL THREE paths through the shared
`_seed_companion_operational_state` helper that recruit_companion already implemented
inline, None-guarded so an ending-seeded arc/dossier is never overwritten, additive so
old snapshots (arc=None) still load.

F06-3: `companion_advise` (the ONLY companion surface in live use, 54 calls) ignores the
dossier, the approval gauge, and the arc. The fix folds those into its return at near-zero
cost: a stance hint derived from the engine-computed attitude band (reads only the gauge),
the dossier's approval causes for human judgment, and an arc/gate-distance summary — so
100% of existing advise beats get richer. The `deliberate` module stays PURE: with no
dossier/standing passed it is byte-identical to today (the additive guarantee).
"""

import pytest

import companion as companion_mod
import server
from models import Campaign, Character, CompanionArc, CompanionDossier


# ============================================================================
# F06-1 — seeding parity across all three companion-creation paths
# ============================================================================


def test_create_character_companion_seeds_arc_and_dossier(tmp_path, monkeypatch):
    """The DOMINANT path: a companion made via create_character must get a default arc
    AND a (possibly empty-but-present) dossier, so camp/gates/agendas have state to track.
    Before the fix this companion had arc=None / dossier=None (20/20 live snapshots)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Seed")["id"]
    res = server.create_character(cid, "Lyra", kind="companion", class_name="Ranger")
    ch = server.get_character(cid, res["id"])
    assert ch["arc"] is not None, "create_character companion must seed an arc (was None)"
    assert ch["companion_dossier"] is not None, "create_character companion must seed a dossier"
    # the default arc carries a loyalty gate so the bond can deepen at camp
    assert any(g["kind"] == "loyalty" for g in ch["arc"]["arc_gates"])


def test_create_character_player_and_npc_unchanged(tmp_path, monkeypatch):
    """ONLY companions are seeded — a player / npc / monster is byte-identical to today
    (no arc/dossier), so the change is scoped and additive."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Seed")["id"]
    pc = server.get_character(cid, server.create_character(cid, "Hero", kind="player")["id"])
    npc = server.get_character(cid, server.create_character(cid, "Bartender", kind="npc")["id"])
    mon = server.get_character(cid, server.create_character(cid, "Goblin", kind="monster")["id"])
    assert pc["arc"] is None and pc["companion_dossier"] is None
    assert npc["arc"] is None and npc["companion_dossier"] is None
    assert mon["arc"] is None and mon["companion_dossier"] is None


def test_create_character_companion_synthesizes_dossier_from_prose(tmp_path, monkeypatch):
    """A companion created with personality prose gets that folded into a terse camp prompt —
    the same synthesis recruit_companion does, so the dossier isn't a blank slate."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Seed")["id"]
    res = server.create_character(
        cid, "Sable", kind="companion",
        biography="A defrocked cleric haunted by a vow she broke. Speaks in clipped warnings.",
    )
    d = server.get_character(cid, res["id"])["companion_dossier"]
    assert d is not None
    # biography prose becomes a terse camp prompt (the create_character authoring source)
    assert any("defrocked cleric" in p for p in d["camp_prompts"])


def test_load_canon_character_seeds_arc(tmp_path, monkeypatch):
    """load_canon_character already seeds the DOSSIER but never an ARC — after the fix a
    canon-loaded companion carries both, so camp/gates work on the canon path too."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate", ending="netherbrain-destroyed-heroes-live")["campaign_id"]
    res = server.load_canon_character(bg, "Gale", kind="companion", add_to_party=True)
    ch = server.get_character(bg, res["id"])
    assert ch["arc"] is not None, "canon-loaded companion must seed an arc"
    assert ch["companion_dossier"] is not None


def test_load_canon_character_npc_not_seeded(tmp_path, monkeypatch):
    """A canon figure pulled as an NPC (not a companion) gets NO arc — seeding is
    companion-only on every path."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    res = server.load_canon_character(bg, "Gale", kind="npc")
    ch = server.get_character(bg, res["id"])
    assert ch["arc"] is None


def test_seeding_never_overwrites_an_ending_seeded_arc(tmp_path, monkeypatch):
    """The None-guard discipline: an ending-seeded companion keeps its AUTHORED arc when
    recruited — the helper never clobbers a richer, character-specific arc."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate", ending="illithid-ascension")["campaign_id"]
    before = server.get_character(bg, "npc-the-emperor")
    before_arc = before.get("arc")
    server.recruit_companion(bg, "npc-the-emperor", class_name="Wizard", abilities={"intelligence": 18})
    after = server.get_character(bg, "npc-the-emperor")
    # the seeded dossier survives (existing contract), and IF an arc was authored it is untouched
    assert "dominion" in after["companion_dossier"]["values"]
    if before_arc is not None:
        assert after["arc"] == before_arc


def test_recruit_still_seeds_arc_and_dossier(tmp_path, monkeypatch):
    """The path that ALREADY worked must keep working after the extract-shared-helper refactor."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Seed")["id"]
    npc = server.create_character(cid, "Bram", kind="npc")["id"]
    server.update_character(cid, npc, {
        "backstory": "A grizzled sellsword. Quiet about his past.",
        "memory": ["owes a debt to the Harpers"],
    })
    out = server.recruit_companion(cid, npc, class_name="Fighter")
    assert out["arc_seeded"] is True and out["dossier_seeded"] is True
    ch = server.get_character(cid, npc)
    assert ch["arc"] is not None
    assert "A grizzled sellsword" in ch["companion_dossier"]["camp_prompts"]
    assert "owes a debt to the Harpers" in ch["companion_dossier"]["camp_prompts"]


def test_old_snapshot_with_arcless_companion_round_trips():
    """ADDITIVE round-trip: an old snapshot whose companion predates seeding (arc=None,
    companion_dossier=None) must still deserialize unchanged — the helper runs at CREATE
    time, never breaking a load of pre-fix state."""
    ch = Character(name="Old", kind="companion", attitude_value=10)
    data = ch.model_dump(mode="json")
    assert data["arc"] is None and data["companion_dossier"] is None
    again = Character.model_validate(data)
    assert again.arc is None and again.companion_dossier is None


# ============================================================================
# F06-3 — companion_advise reads dossier / approval-gauge / arc
# ============================================================================


def test_deliberate_with_no_dossier_or_standing_is_byte_identical():
    """The PURE-MODULE additive guarantee: deliberate() called without the new dossier/
    standing args returns EXACTLY today's keys — no new keys leak when no enrichment data
    is passed."""
    comp = Character(name="Vesper", kind="companion", voice_id="companion-default",
                     personality="a warm field medic who argues for mercy")
    frame = companion_mod.deliberate(comp, situation="a cornered goblin begs", callbacks=[])
    assert set(frame) == {"companion", "voice_id", "personality", "callbacks", "prompt"}


def test_deliberate_folds_standing_and_dossier_when_passed():
    """deliberate() given a standing band + dossier approval causes surfaces them in the
    return for human judgment — the engine reads the gauge, the DM judges the cause."""
    comp = Character(name="Astra", kind="companion", voice_id="companion-default",
                     personality="a sharp-tongued sorceress")
    dossier = CompanionDossier(approval_likes=["bold defiance of tyrants"],
                               approval_dislikes=["needless cruelty"])
    frame = companion_mod.deliberate(
        comp, situation="the duke offers a bribe",
        standing={"band": "warm", "attitude_value": 45},
        dossier=dossier,
    )
    assert frame["standing"]["band"] == "warm"
    assert frame["standing"]["attitude_value"] == 45
    assert "bold defiance of tyrants" in frame["approval_likes"]
    assert "needless cruelty" in frame["approval_dislikes"]
    # the stance is reflected into the prompt so the DM voices a companion who already has a leaning
    assert "warm" in frame["prompt"]


def test_companion_advise_surfaces_band_and_approval_causes(tmp_path, monkeypatch):
    """The live surface (companion_advise) now reads the seeded dossier + the approval gauge
    + the arc and folds them into its return — every existing advise beat gets richer at
    near-zero token cost."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Advise")["id"]
    res = server.create_character(cid, "Mira", kind="companion", class_name="Cleric")
    comp_id = res["id"]
    # author the dossier's approval causes + nudge the gauge into a clear band
    server.update_character(cid, comp_id, {
        "attitude_value": 55,
        "companion_dossier": {"approval_likes": ["protecting the weak"],
                              "approval_dislikes": ["betraying a trust"]},
    })
    out = server.companion_advise(cid, comp_id, situation="a beggar asks for coin")
    assert out["standing"]["attitude_value"] == 55
    assert out["standing"]["band"]  # a non-empty band label derived from the gauge
    assert "protecting the weak" in out["approval_likes"]
    assert "betraying a trust" in out["approval_dislikes"]
    # the seeded default arc is summarized (gate + how far the next locked gate is)
    assert out["arc"] is not None
    assert "gates" in out["arc"]


def test_companion_advise_without_dossier_or_arc_degrades_cleanly(tmp_path, monkeypatch):
    """A companion with no dossier and no arc (e.g. an old snapshot loaded pre-backfill, or
    an NPC mid-promotion) advises WITHOUT the optional keys rather than erroring — the
    enrichment is best-effort and the base frame always returns."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Advise")["id"]
    # build a bare companion record directly (no seeding) to mimic a pre-fix snapshot
    c = server.load_campaign(cid)
    bare = Character(name="Husk", kind="companion", voice_id="companion-default")
    c.characters[bare.id] = bare
    c.party.append(bare.id)
    server.save_campaign(c)
    out = server.companion_advise(cid, bare.id, situation="a quiet road")
    assert out["voice_id"] and "prompt" in out  # base frame still returns
    assert out["standing"]["attitude_value"] == 0  # gauge always readable
    assert out["arc"] is None  # no arc -> summary is None, not an error


def test_companion_advise_standing_band_tracks_the_gauge(tmp_path, monkeypatch):
    """The band is a deterministic read of attitude_value only (the gauge) — a hostile and a
    devoted companion get DIFFERENT band labels from the SAME tool, so the DM voices the
    right leaning."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Advise")["id"]
    cold = server.create_character(cid, "Frost", kind="companion")["id"]
    warm = server.create_character(cid, "Ember", kind="companion")["id"]
    server.update_character(cid, cold, {"attitude_value": -70})
    server.update_character(cid, warm, {"attitude_value": 80})
    cold_band = server.companion_advise(cid, cold, situation="x")["standing"]["band"]
    warm_band = server.companion_advise(cid, warm, situation="x")["standing"]["band"]
    assert cold_band != warm_band
