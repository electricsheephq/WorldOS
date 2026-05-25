"""#68 — CompanionDossier schema + content seeding.

A companion's structured OPERATIONAL identity (wound/wants/fears/values/approval causes/
banter tags/camp prompts/relationships) is a REAL engine fact the living-world systems act
on — not prose buried in `personality`/`backstory` or a one-off `ArcGate.note`. These tests
guard:

  * the new `companion_dossier` field is ADDITIVE — an old snapshot with no dossier (and a
    seeded dossier with any subset of fields) deserializes unchanged;
  * strict validation still holds for committed content (a typo'd field is rejected);
  * the three seeding paths populate correctly — `npc_roster`, canon character JSON, and an
    ending's `companion_seeds` — and a MALFORMED optional dossier DEGRADES (skipped) exactly
    like a malformed `companion_seeds` arc, never aborting world creation;
  * `recruit_companion` synthesizes a minimal dossier from existing prose/memory only when
    none exists (never overwriting a seeded one), and `get_character` returns it via dump.
"""

import json

import pytest
from pydantic import ValidationError

import content
import server
from models import Campaign, Character, CompanionDossier


# --- additive default: a Character with no dossier --------------------------

def test_character_dossier_defaults_none():
    ch = Character(name="Hero")
    assert ch.companion_dossier is None


def test_old_snapshot_without_dossier_field_deserializes_unchanged():
    """An existing snapshot predates the `companion_dossier` field — it must load with
    companion_dossier=None and round-trip identically (the additive-default contract)."""
    ch = Character(name="Hero", kind="companion", attitude_value=10)
    data = ch.model_dump(mode="json")
    old = {k: v for k, v in data.items() if k != "companion_dossier"}  # simulate a pre-#68 snapshot
    assert "companion_dossier" not in old
    reloaded = Character.model_validate(old)
    assert reloaded.companion_dossier is None
    # full round-trip stays stable
    assert Character.model_validate(reloaded.model_dump(mode="json")).companion_dossier is None


def test_old_campaign_snapshot_with_dossierless_characters_loads():
    """A whole Campaign snapshot whose characters carry no dossier deserializes unchanged."""
    c = Campaign(title="Pre-#68")
    ch = Character(name="Ally", kind="companion")
    c.characters[ch.id] = ch
    c.party.append(ch.id)
    raw = c.model_dump(mode="json")
    # strip the field from every character, as a genuinely old snapshot would lack it
    for cd in raw["characters"].values():
        cd.pop("companion_dossier", None)
    reloaded = Campaign.model_validate(raw)
    assert reloaded.characters[ch.id].companion_dossier is None


# --- the model: empty default + strict validation ---------------------------

def test_empty_dossier_is_all_empty():
    d = CompanionDossier()
    assert d.wound == ""
    assert d.wants == [] and d.fears == [] and d.values == []
    assert d.approval_likes == [] and d.approval_dislikes == []
    assert d.banter_tags == [] and d.camp_prompts == []
    assert d.relationships == {}


def test_dossier_round_trips_a_full_payload():
    payload = {
        "wound": "lost someone at the Drowning",
        "wants": ["hold the seal", "spare the Choir's victims"],
        "fears": ["becoming what she hunts"],
        "values": ["mercy", "duty"],
        "approval_likes": ["protecting refugees"],
        "approval_dislikes": ["cruelty to pawns"],
        "banter_tags": ["war_guilt", "mercy_vs_duty"],
        "camp_prompts": ["asks what mercy costs when the enemy was once innocent"],
        "relationships": {"npc-jaheira": "old ally"},
    }
    d = CompanionDossier.model_validate(payload)
    assert d.model_dump() == {**CompanionDossier().model_dump(), **payload}


def test_dossier_rejects_unknown_field_for_committed_content():
    # extra="forbid": a typo'd field (e.g. `wantz`) is rejected at author time, so a bad
    # dossier in a SHIPPED seed shows up in tests rather than silently vanishing.
    with pytest.raises(ValidationError):
        CompanionDossier(wantz=["oops"])  # type: ignore[call-arg]


# --- seeding path 1: npc_roster ---------------------------------------------

def test_seed_world_loads_roster_dossier():
    # The baldurs-gate Minsc roster entry ships an OPTIONAL `dossier` (via the short alias).
    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w)
    minsc = next(ch for ch in c.characters.values() if ch.name == "Minsc and Boo")
    assert minsc.companion_dossier is not None
    assert "go_for_the_eyes" in minsc.companion_dossier.banter_tags
    assert minsc.companion_dossier.wound  # a wound clause is present
    # a roster NPC WITHOUT a dossier stays None (additive — today's behavior)
    jaheira = next(ch for ch in c.characters.values() if ch.name == "Jaheira")
    assert jaheira.companion_dossier is None


def test_seed_world_roster_dossier_degrades_on_malformed(tmp_path, monkeypatch):
    # A present-but-malformed roster dossier must SKIP (the NPC gets none), never abort the
    # whole world seed — mirroring the companion_seeds / world_state degrade guards. A valid
    # sibling roster NPC is unaffected.
    monkeypatch.setenv("CLAWDND_CONTENT_DIR", str(tmp_path))
    (tmp_path / "worlds" / "w").mkdir(parents=True)
    world = {
        "id": "w", "schema_version": 1, "name": "W", "ruleset": "SRD 5.2",
        "regions": [{"id": "r1", "name": "Start"}],
        "starting_options": [{"location_id": "r1"}],
        "npc_roster": [
            {"id": "npc-bad", "name": "Bad", "dossier": {"wound": "x", "nope": "typo"}},   # forbidden key -> skip
            {"id": "npc-bad2", "name": "Bad2", "dossier": "not even an object"},            # wrong type -> skip
            {"id": "npc-good", "name": "Good", "dossier": {"values": ["honor"]}},           # valid -> applied
        ],
    }
    (tmp_path / "worlds" / "w" / "world.json").write_text(json.dumps(world), encoding="utf-8")
    c = content.seed_world(world)  # must NOT raise
    assert c.characters["npc-bad"].companion_dossier is None
    assert c.characters["npc-bad2"].companion_dossier is None
    assert c.characters["npc-good"].companion_dossier is not None
    assert c.characters["npc-good"].companion_dossier.values == ["honor"]


# --- seeding path 2: canon character JSON -----------------------------------

def test_canon_character_record_carries_a_dossier():
    # Gale ships a `companion_dossier` in his canon JSON (and is NOT in the roster, so a
    # load_canon_character call takes the FRESH-load path, not the already_present short-circuit).
    rec = content.load_canon_character("baldurs-gate", "Gale")
    assert rec is not None
    d = content._coerce_dossier(rec.get("companion_dossier"), where="test")
    assert d is not None and "redemption" in d.values


def test_load_canon_character_populates_dossier(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate", ending="netherbrain-destroyed-heroes-live")["campaign_id"]
    res = server.load_canon_character(bg, "Gale", kind="companion", add_to_party=True)
    assert not res.get("already_present")  # Gale isn't rostered -> real fresh load
    sheet = server.get_character(bg, res["id"])
    assert "companion_dossier" in sheet  # get_character returns it through the normal dump
    assert sheet["companion_dossier"]["wound"]
    assert "knowledge" in sheet["companion_dossier"]["values"]


def test_coerce_dossier_degrades_on_malformed():
    # The shared lenient coercer: None -> None; a non-dict -> None; a dict with a forbidden
    # key -> None (all degrade, none raise). A valid dict validates.
    assert content._coerce_dossier(None, where="t") is None
    assert content._coerce_dossier("nope", where="t") is None
    assert content._coerce_dossier({"bad_key": 1}, where="t") is None
    ok = content._coerce_dossier({"wants": ["a"]}, where="t")
    assert ok is not None and ok.wants == ["a"]


# --- seeding path 3: ending companion_seeds ---------------------------------

def test_ending_companion_seed_loads_dossier_alongside_arc():
    # illithid-ascension seeds the Emperor's arc AND a dossier; both must land, and the arc
    # must survive (the dossier is applied independently, not in place of the arc).
    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w, ending="illithid-ascension")
    emp = c.characters["npc-the-emperor"]
    assert emp.arc is not None  # the S4 arc still seeds
    assert emp.companion_dossier is not None
    assert "dominion" in emp.companion_dossier.values


def test_ending_companion_seed_dossier_degrades_independently():
    # A seed whose ARC is malformed but DOSSIER is valid: the dossier still applies (and
    # vice-versa). A malformed dossier on an otherwise-valid arc seed degrades to no dossier,
    # never aborting the overlay.
    c = Campaign(title="W")
    emp = Character(id="npc-x", name="X", kind="npc")
    c.characters["npc-x"] = emp
    overlay = {
        "id": "ov", "name": "Ov",
        "companion_seeds": {
            "npc-x": {
                # a day_reached agenda missing its required `value` -> the ARC degrades...
                "arc": {"agenda": {"trigger": "day_reached"}},
                # ...but the DOSSIER is valid and must still apply
                "dossier": {"values": ["cunning"], "wants": ["the upper hand"]},
            }
        },
    }
    content._apply_ending_overlay(c, overlay)  # must NOT raise
    assert emp.arc is None  # malformed arc was skipped
    assert emp.companion_dossier is not None and emp.companion_dossier.values == ["cunning"]

    # the mirror case: valid arc, malformed dossier -> arc applies, dossier skipped
    c2 = Campaign(title="W2")
    emp2 = Character(id="npc-y", name="Y", kind="npc")
    c2.characters["npc-y"] = emp2
    overlay2 = {
        "id": "ov2", "name": "Ov2",
        "companion_seeds": {
            "npc-y": {
                "arc": {"arc_gates": [{"kind": "loyalty", "threshold": 10}]},
                "dossier": {"bad_field": "typo"},  # forbidden key -> degrade
            }
        },
    }
    content._apply_ending_overlay(c2, overlay2)  # must NOT raise
    assert emp2.arc is not None  # the valid arc applied
    assert emp2.companion_dossier is None  # the malformed dossier was skipped


def test_ending_companion_seed_dossier_only_is_honored():
    # A seed carrying ONLY a dossier (no `arc`) must still apply the dossier — the broadened
    # guard no longer requires an `arc` to be present.
    c = Campaign(title="W")
    ch = Character(id="npc-z", name="Z", kind="npc")
    c.characters["npc-z"] = ch
    overlay = {
        "id": "ov", "name": "Ov",
        "companion_seeds": {"npc-z": {"dossier": {"wound": "a quiet grief"}}},
    }
    content._apply_ending_overlay(c, overlay)  # must NOT raise
    assert ch.arc is None  # no arc was given
    assert ch.companion_dossier is not None and ch.companion_dossier.wound == "a quiet grief"


# --- recruit_companion: synthesize-when-absent, never-overwrite -------------

def test_recruit_synthesizes_minimal_dossier_from_existing_state(tmp_path, monkeypatch):
    # A freshly-recruited companion with no seeded dossier gets a MINIMAL one synthesized
    # from what the record already carries (a backstory clause + memory facts -> camp prompts),
    # so it isn't a blank slate at camp.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Dossier")["id"]
    npc = server.create_character(cid, "Bram", kind="npc")["id"]
    server.update_character(cid, npc, {
        "backstory": "A grizzled sellsword. Quiet about his past.",
        "memory": ["owes a debt to the Harpers"],
    })
    out = server.recruit_companion(cid, npc, class_name="Fighter")
    assert out.get("dossier_seeded") is True
    d = server.get_character(cid, npc)["companion_dossier"]
    assert d is not None
    # the backstory's first clause + the memory fact became terse camp prompts
    assert "A grizzled sellsword" in d["camp_prompts"]
    assert "owes a debt to the Harpers" in d["camp_prompts"]


def test_recruit_never_overwrites_a_seeded_dossier(tmp_path, monkeypatch):
    # An ending-seeded companion (the Emperor carries a dossier from illithid-ascension) keeps
    # its authored dossier when recruited — the synthesis is guarded on `companion_dossier is None`.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate", ending="illithid-ascension")["campaign_id"]
    server.recruit_companion(bg, "npc-the-emperor", class_name="Wizard", abilities={"intelligence": 18})
    d = server.get_character(bg, "npc-the-emperor")["companion_dossier"]
    assert "dominion" in d["values"]  # the seed, NOT a synthesized minimal dossier


def test_recruit_synthesized_dossier_is_terse(tmp_path, monkeypatch):
    # The synthesized dossier must stay OPERATIONAL, not a copy of the whole biography: it caps
    # the camp prompts and truncates a long backstory clause rather than pasting prose.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Terse")["id"]
    npc = server.create_character(cid, "Verbose", kind="npc")["id"]
    long_clause = "x" * 500
    server.update_character(cid, npc, {
        "backstory": long_clause,
        "memory": [f"fact {i}" for i in range(10)],
    })
    server.recruit_companion(cid, npc, class_name="Fighter")
    d = server.get_character(cid, npc)["companion_dossier"]
    assert len(d["camp_prompts"]) <= 4  # capped, not the whole memory list
    assert all(len(p) <= 200 for p in d["camp_prompts"])  # each clause truncated
    # the dossier does NOT duplicate the long backstory wholesale
    assert long_clause not in d["camp_prompts"]
