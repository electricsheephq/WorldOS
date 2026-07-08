#!/usr/bin/env python3
"""Tests for the HV1 per-artifact eval instruments (#1323).

Pure-stdlib (sqlite3 + json); imports neither the engine nor the viewer, and never invokes a live
scorer (the panel uses WORLDOS_ARTIFACT_PANEL_DRYRUN for offline wiring). Run:
    uv run --directory servers/engine python -m pytest qa/test_artifact_evals.py -q -p no:xdist
or simply:
    python3 -m pytest qa/test_artifact_evals.py -q
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
REPO = QA_DIR.parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import scoring_config_version as scv  # noqa: E402
import artifact_score  # noqa: E402
import artifact_snapshot_reader as snap_reader  # noqa: E402
import artifact_calibration_panel as panel  # noqa: E402


# ---------------------------------------------------------------------------
# THE LOAD-BEARING INVARIANT: the sc_/lc_ engine-duo rulers are BYTE-UNCHANGED
# ---------------------------------------------------------------------------
# HV1 adds a NEW ruler family (ac_) and MUST NOT perturb the existing story/mech/angry rulers — that
# would silently re-version every historical engine-duo score. These pin the exact current hashes
# (origin/main @ the HV1 branch point). If a future edit changes them, this test goes RED and forces a
# conscious re-baseline (the same discipline test_scores_db_comparability enforces for content edits).
EXPECTED_SC = "sc_38b768e2fd1b"  # re-baselined #1427: release_readiness.py touched by #1417/#1414
EXPECTED_LC = "lc_b031bd9f47e1"  # ("qa: auto-persist scores rows for the manual-append bucket",
# commit 9f244613) — that PR only ADDS a new --scores-db CLI arg and an auto-persist call for the
# RRI row AFTER `result` is computed and written to --out; it does not touch any of the 11 RRI
# gates, thresholds, or scoring logic. Verdict: NON-SEMANTIC to scoring — zero effect on what an
# RRI/lens number MEANS — so only sc_ (which includes release_readiness.py) moves; lc_ (the 8
# lens-only files) is confirmed BYTE-IDENTICAL (unchanged from the #1360 re-baseline), matching
# scoring_config_version()'s file-byte hashing (it reads p.read_bytes(), so any edit to a listed
# file re-versions sc_ regardless of semantic effect — this is a deliberate restamp of a
# no-semantic-change edit, not a ruler recalibration).


def test_engine_duo_rulers_are_byte_unchanged():
    assert scv.scoring_config_version() == EXPECTED_SC, (
        "the FULL (sc_) engine-duo ruler changed — HV1 must be additive and NOT touch "
        "SCORING_CONFIG_FILES; re-baseline deliberately if this is intended"
    )
    assert scv.lens_config_version() == EXPECTED_LC, (
        "the LENS (lc_) engine-duo ruler changed — HV1 must not touch LENS_CONFIG_FILES"
    )


def test_scoring_config_files_lists_unchanged():
    # HV1 must not have appended anything to the engine-duo file lists.
    assert scv.SCORING_CONFIG_FILES == [
        "rubric.md", "rubric_tolkien.md", "rubric_angry_dm.md", "rubric_angry_dm.src.md",
        "score_schema.json", "score_schema_tolkien.json", "score_schema_angry_dm.json",
        "assert_behavioral.py", "release_readiness.py",
    ]
    assert "rubric_artifact_quest.md" not in scv.SCORING_CONFIG_FILES
    assert scv.LENS_CONFIG_FILES == [n for n in scv.SCORING_CONFIG_FILES if n != "release_readiness.py"]


# ---------------------------------------------------------------------------
# The ARTIFACT ruler (ac_) — its own family, deterministic + content-sensitive
# ---------------------------------------------------------------------------
def test_artifact_ruler_is_deterministic_and_prefixed():
    v1 = scv.artifact_config_version()
    v2 = scv.artifact_config_version()
    assert v1 == v2
    assert v1.startswith("ac_") and len(v1) == 15, v1  # 'ac_' + 12 hex


def test_artifact_ruler_lists_all_eight_class_files():
    files = set(scv.ARTIFACT_CONFIG_FILES)
    for cls in ("quest", "npc", "location", "encounter"):
        assert f"rubric_artifact_{cls}.md" in files
        assert f"score_schema_artifact_{cls}.json" in files
    assert len(scv.ARTIFACT_CONFIG_FILES) == 8


def test_artifact_ruler_changes_when_an_artifact_rubric_changes(tmp_path):
    root = tmp_path
    for name in scv.ARTIFACT_CONFIG_FILES:
        (root / name).write_text("baseline\n", encoding="utf-8")
    base = scv.artifact_config_version(root)
    (root / "rubric_artifact_quest.md").write_text("recalibrated\n", encoding="utf-8")
    assert scv.artifact_config_version(root) != base


def test_editing_artifact_rubric_does_not_touch_sc_or_lc(tmp_path):
    # The artifact and engine-duo families are independent: an artifact-only file set can't move sc_/lc_
    # (they read a DIFFERENT file list). Prove they share no files.
    assert set(scv.ARTIFACT_CONFIG_FILES).isdisjoint(set(scv.SCORING_CONFIG_FILES))


# ---------------------------------------------------------------------------
# The `artifacts` table — additive, separate from runs, roundtrips
# ---------------------------------------------------------------------------
def test_artifacts_table_columns_registered_with_types():
    for col in ("class", "run_id", "world", "dims_json", "overall", "panel_id",
                "scorer_model", "ac_ruler", "is_control"):
        assert col in scores_db.ARTIFACT_COLUMNS, f"{col} missing"
    assert scores_db._artifact_coltype("overall") == "REAL"
    assert scores_db._artifact_coltype("control_anchor") == "REAL"
    assert scores_db._artifact_coltype("is_control") == "INTEGER"
    assert scores_db._artifact_coltype("class") == "TEXT"


def test_add_artifact_and_fetch_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    dims = {"hook_strength": 4.2, "objective_clarity": 3.8, "consequence_weight": 4.0,
            "stakes_escalation": 3.5, "reusability": 4.1}
    scores_db.add_artifact(
        "quest:bg:x", db_path=db, **{"class": "quest"}, world="baldurs-gate",
        run_id="ow-fixC", sha="abc1234", dims_json=dims, overall=4.0,
        panel_id="cal-quest-1", scorer_model="sonnet", source_path="qa/x.json",
    )
    rows = scores_db.fetch_artifacts(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["artifact_id"] == "quest:bg:x"
    assert r["class"] == "quest"
    assert r["overall"] == 4.0
    assert r["run_id"] == "ow-fixC"
    assert json.loads(r["dims_json"]) == dims
    # ac_ruler is auto-stamped
    assert r["ac_ruler"] == scv.artifact_config_version()


def test_add_artifact_rejects_unknown_field(tmp_path):
    with pytest.raises(ValueError):
        scores_db.add_artifact("x", db_path=tmp_path / "t.db", bogus=1)


def test_add_artifact_rejects_bad_class(tmp_path):
    with pytest.raises(ValueError):
        scores_db.add_artifact("x", db_path=tmp_path / "t.db", **{"class": "not-a-class"})


def test_all_artifact_classes_accepted(tmp_path):
    db = tmp_path / "t.db"
    for i, c in enumerate(scores_db.ARTIFACT_CLASSES):
        scores_db.add_artifact(f"a{i}", db_path=db, **{"class": c})
    assert len(scores_db.fetch_artifacts(db)) == len(scores_db.ARTIFACT_CLASSES)


def test_is_control_bool_coerced_to_int(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_artifact("c", db_path=db, **{"class": "npc"}, is_control=True)
    assert scores_db.fetch_artifacts(db)[0]["is_control"] == 1


def test_artifacts_table_does_not_touch_runs(tmp_path):
    # Writing artifacts must not create/modify runs rows, and runs must still work independently.
    db = tmp_path / "t.db"
    scores_db.add_artifact("q", db_path=db, **{"class": "quest"}, overall=4.0)
    scores_db.add_run("duo-x", db_path=db, surface="engine-duo", story_overall=4.1)
    assert len(scores_db.fetch_artifacts(db)) == 1
    assert len(scores_db.fetch_rows(db)) == 1
    # the two tables are distinct
    conn = scores_db.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"runs", "artifacts"} <= tables


def test_artifacts_table_alters_into_an_old_db(tmp_path):
    # A db that has only `runs` gets the `artifacts` table created on connect (additive).
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE runs ("run_id" TEXT PRIMARY KEY, "surface" TEXT)')
    conn.commit()
    conn.close()
    conn = scores_db.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "artifacts" in tables


def test_new_artifact_column_alters_into_existing_artifacts_table(tmp_path):
    # An artifacts table missing a newer ARTIFACT_COLUMNS entry gets it ALTER-added.
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE artifacts ("artifact_id" TEXT PRIMARY KEY, "class" TEXT)')
    conn.commit()
    conn.close()
    conn = scores_db.connect(db)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(artifacts)")}
    conn.close()
    assert {"overall", "dims_json", "ac_ruler", "is_control"} <= cols


def test_render_artifacts_markdown(tmp_path):
    db = tmp_path / "t.db"
    md = tmp_path / "art.md"
    scores_db.add_artifact("quest:bg:r", db_path=db, **{"class": "quest"},
                           world="baldurs-gate", overall=4.1, panel_id="cal-q")
    text = scores_db.render_artifacts_markdown(db, md)
    assert "Per-Artifact Scores Ledger" in text
    assert "quest:bg:r" in text
    assert md.exists()


# ---------------------------------------------------------------------------
# Schema validation: the shared envelope + the per-class score schemas
# ---------------------------------------------------------------------------
def test_shared_artifact_schema_is_canonical_hv2_envelope_with_hv1_source():
    # Post-#1329 reconciliation: main's HV2-authored schema is CANONICAL. Assert its shape + that HV1's
    # ADDITIVE optional provenance.source was folded in (never made required).
    schema = json.loads((REPO / "data" / "library" / "artifact_schema.json").read_text())
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"artifact_id", "class", "world", "provenance", "payload", "scores"}
    assert schema["properties"]["class"]["enum"] == ["quest", "npc", "location", "encounter"]
    prov = schema["properties"]["provenance"]
    assert prov["additionalProperties"] is False
    assert set(prov["required"]) == {"campaign_id", "run_id", "sha", "extracted_at"}
    # HV1's additive source: present, optional (NOT in provenance.required), nullable string.
    assert "source" in prov["properties"], "HV1's additive provenance.source must be in the schema"
    assert "source" not in prov["required"], "source must be OPTIONAL (HV2's extractor doesn't emit it)"
    assert prov["properties"]["source"]["type"] == ["string", "null"]
    # canonical per-class payload definitions exist (HV3 will bind them via if/then).
    for cls in ("quest", "npc", "location", "encounter"):
        assert f"{cls}_payload" in schema["definitions"]


def test_each_class_score_schema_matches_its_rubric_dims():
    # The score schema's required dims must equal the dims the card/rubric names, per class.
    expected = {
        "quest": {"hook_strength", "objective_clarity", "consequence_weight",
                  "stakes_escalation", "reusability"},
        "npc": {"voice_distinctiveness", "motivation_coherence", "arc_potential", "reusability"},
        "location": {"identity", "affordances", "atmosphere", "reusability"},
        "encounter": {"composition_interest", "tactical_texture", "stakes", "reusability"},
    }
    for cls, dims in expected.items():
        _, schema_name = artifact_score.RUBRIC_FOR_CLASS[cls]
        schema = json.loads((QA_DIR / schema_name).read_text())
        got = set(schema["properties"]["scores"]["required"])
        assert got == dims, f"{cls}: schema dims {got} != rubric dims {dims}"
        # plain-number one-decimal contract (no multipleOf footgun)
        for d in dims:
            spec = schema["properties"]["scores"]["properties"][d]
            assert spec["type"] == "number" and spec["minimum"] == 1 and spec["maximum"] == 5
            assert "multipleOf" not in spec, "multipleOf is an IEEE-754 footgun; keep it a plain number"


def test_artifact_config_files_match_rubric_pairs():
    # ARTIFACT_CONFIG_FILES must be exactly the rubric+schema files artifact_score pairs per class.
    referenced = set()
    for rubric, schema in artifact_score.RUBRIC_FOR_CLASS.values():
        referenced.add(rubric)
        referenced.add(schema)
    assert referenced == set(scv.ARTIFACT_CONFIG_FILES)


def test_all_class_rubric_and_schema_files_exist():
    for cls in scores_db.ARTIFACT_CLASSES:
        rubric, schema = artifact_score.RUBRIC_FOR_CLASS[cls]
        assert (QA_DIR / rubric).is_file(), rubric
        assert (QA_DIR / schema).is_file(), schema


# ---------------------------------------------------------------------------
# The card builder: disguise-safe (payload only — no provenance / id / control marker)
# ---------------------------------------------------------------------------
def test_build_card_carries_only_payload():
    # Canonical npc payload (personality is an object; the disguise must hide provenance + id + control).
    artifact = {
        "artifact_id": "control:npc:baldurs-gate:npc-jaheira",
        "class": "npc", "world": "baldurs-gate",
        "provenance": {"campaign_id": "camp-secret", "run_id": "run-secret", "sha": None,
                       "extracted_at": "canon", "source": "world.json:npc_roster"},
        "payload": {"id": "npc-jaheira", "name": "Jaheira", "voice_id": "npc-elder",
                    "personality": {"summary": "Dry, fierce."},
                    "attitude_arc": {"start": 0, "end": 0}, "final_status": "canon-roster",
                    "dialogue_snippets": [], "role": "High Harper"},
        "scores": None,
    }
    card = artifact_score.build_card(artifact)
    assert "Jaheira" in card
    for leak in ("control:", "provenance", "camp-secret", "run-secret", "artifact_id",
                 "world.json", artifact["artifact_id"]):
        assert leak.lower() not in card.lower(), f"disguise leak: {leak!r}"


def test_load_artifact_validates_required_keys(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"class": "quest"}))  # missing artifact_id + payload
    with pytest.raises(ValueError):
        artifact_score.load_artifact(bad)


def test_load_artifact_validates_canonical_payload_shape(tmp_path):
    # The explicit class-payload guard (until #1329/HV3 binds if/then): a quest payload missing a
    # canonical required field fails LOUDLY; strict_payload=False disables it.
    art = {"artifact_id": "quest:c:q", "class": "quest", "world": "w",
           "provenance": {"campaign_id": "c", "run_id": None, "sha": None, "extracted_at": "x"},
           "payload": {"id": "q", "name": "Q"},  # missing objectives/resolution_status/etc.
           "scores": None}
    p = tmp_path / "q.json"; p.write_text(json.dumps(art))
    with pytest.raises(ValueError, match="canonical required field"):
        artifact_score.load_artifact(p)
    # non-strict load tolerates it (the card is field-tolerant)
    obj = artifact_score.load_artifact(p, strict_payload=False)
    assert obj["class"] == "quest"


# ---------------------------------------------------------------------------
# The thin snapshot reader: DELEGATES to HV2's canonical extractor (#1329)
# ---------------------------------------------------------------------------
def _mini_snapshot() -> dict:
    # Shaped for HV2's extract_quests/extract_npcs (the canonical field names they read).
    return {
        "id": "camp_test", "world_id": "baldurs-gate", "engine_sha": "deadbeef",
        "consequences": [],
        "quests": {"q1": {"id": "q1", "title": "The Ledger",
                          "objectives": ["step one"], "completed_objectives": [],
                          "status": "active", "evolves_to": ""}},
        "characters": {
            "npc-a": {"id": "npc-a", "kind": "npc", "name": "Rael",
                      "personality": "Kind face, clean hands.", "attitude_value": 2, "voice_id": "v"},
            "pc-1": {"id": "pc-1", "kind": "pc", "name": "Hero"},
        },
    }


def test_snapshot_reader_delegates_to_hv2_canonical_envelope():
    arts = snap_reader.extract(_mini_snapshot(), world="baldurs-gate", run_id="run-1",
                               extracted_at="2026-07-03T00:00:00Z", classes=("quest", "npc"))
    by_class = {a["class"] for a in arts}
    assert by_class == {"quest", "npc"}
    quest = next(a for a in arts if a["class"] == "quest")
    # HV2 canonical: artifact_id keyed on campaign_id; canonical payload field names.
    assert quest["artifact_id"] == "quest:camp_test:q1"
    assert quest["payload"]["name"] == "The Ledger"
    assert set(quest["payload"]) >= {"id", "name", "objectives", "completed_objectives",
                                     "resolution_status", "evolves_to", "consequences"}
    # canonical provenance: non-null campaign_id, caller-supplied extracted_at (deterministic).
    assert quest["provenance"]["campaign_id"] == "camp_test"
    assert quest["provenance"]["extracted_at"] == "2026-07-03T00:00:00Z"
    assert quest["provenance"]["run_id"] == "run-1"
    assert quest["scores"] is None
    # excludes the PC (only kind==npc extracted)
    npc_names = {a["payload"]["name"] for a in arts if a["class"] == "npc"}
    assert npc_names == {"Rael"}


def test_snapshot_reader_extracted_at_is_deterministic():
    a1 = snap_reader.extract(_mini_snapshot(), world="w", extracted_at="FIXED", classes=("quest",))
    a2 = snap_reader.extract(_mini_snapshot(), world="w", extracted_at="FIXED", classes=("quest",))
    assert a1 == a2  # byte-identical when extracted_at is pinned (no wall-clock inside)


def test_snapshot_reader_extracted_artifacts_conform_to_schema():
    schema = json.loads((REPO / "data" / "library" / "artifact_schema.json").read_text())
    req = set(schema["required"])
    enum = set(schema["properties"]["class"]["enum"])
    for a in snap_reader.extract(_mini_snapshot(), world="baldurs-gate", extracted_at="x"):
        assert req <= set(a), f"missing envelope keys: {req - set(a)}"
        assert a["class"] in enum
        assert isinstance(a["provenance"]["campaign_id"], str) and a["provenance"]["campaign_id"]


# ---------------------------------------------------------------------------
# The controls: disguised canon exists for every class; identity map is OUTSIDE the panel dir
# ---------------------------------------------------------------------------
def test_committed_controls_cover_every_class():
    cdir = QA_DIR / "artifact_controls"
    assert cdir.is_dir(), "controls must be committed"
    for cls in scores_db.ARTIFACT_CLASSES:
        matches = list(cdir.glob(f"control__{cls}__*.json"))
        assert len(matches) >= 2, f"need >=2 {cls} controls, found {len(matches)}"


def test_committed_controls_satisfy_canonical_envelope():
    # Every committed control must validate against the CANONICAL (HV2-authored) envelope — non-null
    # campaign_id, provenance additionalProperties:false (so HV1's `source` must be a schema-known key).
    schema = json.loads((REPO / "data" / "library" / "artifact_schema.json").read_text())
    prov_req = set(schema["properties"]["provenance"]["required"])
    prov_props = set(schema["properties"]["provenance"]["properties"])
    for p in (QA_DIR / "artifact_controls").glob("control__*.json"):
        a = json.loads(p.read_text())
        assert set(schema["required"]) <= set(a)
        prov = a["provenance"]
        assert prov_req <= set(prov)
        assert isinstance(prov["campaign_id"], str) and prov["campaign_id"]  # non-null, minLength 1
        assert set(prov) <= prov_props, f"{p.name}: provenance key not in schema (addlProps:false)"


def test_controls_and_extracted_validate_with_jsonschema_when_available():
    # Bonus strict validation (mirrors HV2's importorskip pattern): full jsonschema.validate on every
    # committed control AND a snapshot-reader extraction, against the canonical envelope.
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "data" / "library" / "artifact_schema.json").read_text())
    validator = jsonschema.Draft7Validator(schema)
    for p in (QA_DIR / "artifact_controls").glob("control__*.json"):
        errs = sorted(e.message for e in validator.iter_errors(json.loads(p.read_text())))
        assert not errs, f"{p.name}: {errs}"
    for a in snap_reader.extract(_mini_snapshot(), world="baldurs-gate", extracted_at="x"):
        errs = sorted(e.message for e in validator.iter_errors(a))
        assert not errs, f"extracted {a['artifact_id']}: {errs}"


def test_control_payloads_carry_no_provenance_leak_into_cards():
    # The disguise requirement is that no PROVENANCE MARKER leaks — the artifact_id (which carries the
    # 'control:' prefix), the 'control:' id prefix itself, and every provenance value. It does NOT
    # forbid the plain word "control" appearing in legitimate story prose (e.g. "the control cradle").
    for p in (QA_DIR / "artifact_controls").glob("control__*.json"):
        a = json.loads(p.read_text())
        card = artifact_score.build_card(a).lower()
        assert a["artifact_id"].lower() not in card
        assert "control:" not in card  # the id-prefix marker, not the English word
        prov = a.get("provenance") or {}
        for v in prov.values():
            if isinstance(v, str) and v and v != prov.get("extracted_at"):
                # the source tag ("world.json:quest_variants" / "canon-derived:set-pieces") must not leak
                assert v.lower() not in card, f"provenance value leaked: {v!r}"


def test_build_controls_is_idempotent_in_process(tmp_path):
    # Regression: encounter_controls must NOT mutate the module-level _ENCOUNTER_CANON singleton
    # (a pop() made a second build() call raise KeyError). Two builds in one process must match.
    import build_artifact_controls as bac  # noqa: E402
    c1, _ = bac.build("baldurs-gate", tmp_path / "one")
    c2, _ = bac.build("baldurs-gate", tmp_path / "two")
    ids1 = sorted(a["artifact_id"] for a in c1)
    ids2 = sorted(a["artifact_id"] for a in c2)
    assert ids1 == ids2 and any(a["class"] == "encounter" for a in c2)


def test_identity_map_is_outside_the_panel_input_dir():
    identity = QA_DIR / "artifact_controls_identity.json"
    assert identity.is_file(), "the control identity map must be committed"
    # It must NOT live inside the scored panel input dir (or a scorer could read it).
    assert identity.parent == QA_DIR
    assert identity.parent != (QA_DIR / "artifact_controls")
    data = json.loads(identity.read_text())
    assert data["noise_law"] == 1.2
    assert len(data["controls"]) >= 8


# ---------------------------------------------------------------------------
# The calibration panel (offline DRYRUN — no live scorer, deterministic)
# ---------------------------------------------------------------------------
def test_calibration_panel_dryrun_is_valid_and_writes_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_ARTIFACT_PANEL_DRYRUN", "1")
    db = tmp_path / "t.db"
    report = panel.run_panel("quest", controls_only=True, panel_size=5, db_path=db)
    assert report["class"] == "quest"
    assert report["panel_size"] == 5
    assert report["dryrun"] is True
    assert report["n_controls"] >= 2
    assert report["controls_in_band"] is True
    assert report["panel_valid"] is True
    # 5 per-scorer rows + 1 bare-artifact_id aggregate row per control (#1355).
    rows = scores_db.fetch_artifacts(db)
    assert len(rows) == report["n_controls"] * (5 + 1)
    assert all(r["is_control"] == 1 for r in rows)
    assert all(r["scorer_model"] == "sonnet" for r in rows)
    # The aggregate row is addressable by the BARE artifact_id (no #panel_id#s{n} suffix) — this is
    # exactly what tools/library/promote.py's bare-id lookup needs with no manual bridge.
    control_aids = {cm["artifact_id"] for cm in report["control_medians"]}
    row_ids = {r["artifact_id"] for r in rows}
    assert control_aids <= row_ids, "every control's bare artifact_id must have a row in scores.db"


def test_calibration_panel_flags_out_of_band_control(tmp_path, monkeypatch):
    # Force a broken band: patch the dryrun stub to score every control at 1.0 (far below the anchor).
    monkeypatch.setenv("WORLDOS_ARTIFACT_PANEL_DRYRUN", "1")
    monkeypatch.setattr(panel, "_dryrun_card",
                        lambda artifact, anchor: {"scores": {}, "overall": 1.0,
                                                  "defects": [], "highlights": [], "verdict": "x"})
    db = tmp_path / "t.db"
    report = panel.run_panel("npc", controls_only=True, panel_size=3, db_path=db, write_db=False)
    assert report["controls_in_band"] is False
    assert report["panel_valid"] is False
    assert len(report["out_of_band"]) >= 1


# ---------------------------------------------------------------------------
# #1380: v2 field-surface parity for quest controls + the band-staleness guard
# ---------------------------------------------------------------------------
def test_quest_controls_carry_v2_field_surface():
    # The #1380 root cause: the quest controls predated extractor v2 (#1368) — real candidates carry
    # `description` + `resolution.wrap_up`, the controls did not, so the field-poor control drifted
    # below band. Every committed quest control must now carry the SAME v2 surface.
    for p in (QA_DIR / "artifact_controls").glob("control__quest__*.json"):
        payload = json.loads(p.read_text())["payload"]
        assert payload.get("description"), f"{p.name}: quest control missing v2 `description`"
        resolution = payload.get("resolution")
        assert isinstance(resolution, dict), f"{p.name}: quest control missing v2 `resolution` object"
        assert resolution.get("wrap_up"), f"{p.name}: quest control has empty `resolution.wrap_up`"
        # wrap_up must be real canon (the outcome lore beats), not a placeholder.
        assert all(isinstance(b, str) and b.strip() for b in resolution["wrap_up"])


def test_v2_quest_control_shape_matches_extractor_payload_keys():
    # A control's payload keys must be a superset of what a live v2 extract emits, so a disguised
    # control presents the same field surface to the scorer as a real candidate of the same class.
    import build_artifact_controls as bac  # noqa: E402
    world = json.loads((REPO / "content" / "worlds" / "baldurs-gate" / "world.json").read_text())
    controls = bac.quest_controls(world, "baldurs-gate")
    v2_keys = {"id", "name", "description", "objectives", "completed_objectives",
               "resolution_status", "resolution", "evolves_to", "consequences"}
    for a in controls:
        assert v2_keys <= set(a["payload"]), (
            f"{a['artifact_id']}: payload missing v2 keys {v2_keys - set(a['payload'])}"
        )


def test_prompt_construction_hash_stable_and_tracks_the_card():
    # The band-drift guard's hash must be deterministic, prefixed, and change iff the card changes.
    a = artifact_score.load_artifact(
        QA_DIR / "artifact_controls" / "control__quest__baldurs-gate__the-shadow-cursed-lands.json"
    )
    h1 = artifact_score.prompt_construction_hash(a)
    assert h1.startswith("ph_") and artifact_score.prompt_construction_hash(a) == h1
    # A payload edit that changes the card must change the hash (proves it tracks the prompt).
    a2 = json.loads(json.dumps(a))
    a2["payload"]["description"] = a2["payload"]["description"] + " (edited)"
    assert artifact_score.prompt_construction_hash(a2) != h1


def test_identity_bands_stamped_with_current_ruler_and_prompt_hash():
    # Every committed control's band must be stamped with the ruler + prompt hash it was derived
    # under, and those stamps must MATCH the current control card (else the panel is already stale).
    identity = json.loads((QA_DIR / "artifact_controls_identity.json").read_text())
    cur_ruler = scv.artifact_config_version()
    for aid, entry in identity["controls"].items():
        assert entry.get("band_ruler") == cur_ruler, f"{aid}: unstamped/stale band_ruler"
        a = artifact_score.load_artifact(QA_DIR / "artifact_controls" / entry["file"])
        assert entry.get("band_prompt_hash") == artifact_score.prompt_construction_hash(a), (
            f"{aid}: band_prompt_hash does not match the committed control card"
        )


def test_calibration_panel_flags_stale_band_even_when_in_band(tmp_path, monkeypatch):
    # The guard's whole point: a band derived under a DIFFERENT prompt construction is untrustworthy
    # even if the control happens to land inside it. Corrupt one control's stamped prompt hash; under
    # dryrun the control still scores at the anchor (in band), but the panel must report it STALE with
    # a NAMED reason and fail — turning silent drift into a named error (#1380).
    monkeypatch.setenv("WORLDOS_ARTIFACT_PANEL_DRYRUN", "1")
    identity = json.loads((QA_DIR / "artifact_controls_identity.json").read_text())
    for entry in identity["controls"].values():
        if entry["class"] == "quest":
            entry["band_prompt_hash"] = "ph_stale0000000"
            break
    stale_identity = tmp_path / "identity.json"
    stale_identity.write_text(json.dumps(identity))
    monkeypatch.setattr(panel, "IDENTITY_PATH", stale_identity)

    report = panel.run_panel("quest", controls_only=True, panel_size=3,
                             db_path=tmp_path / "t.db", write_db=False)
    assert report["controls_in_band"] is True, "dryrun scores at anchor — bands are numerically fine"
    assert report["panel_valid"] is False, "a stale band must invalidate the panel"
    assert len(report["stale_bands"]) == 1
    assert "different prompt construction" in report["stale_bands"][0]["reason"]


def test_calibration_panel_valid_when_stamps_match():
    # Sanity converse: with the committed (matching) stamps and dryrun scoring, no control is stale.
    import os
    os.environ["WORLDOS_ARTIFACT_PANEL_DRYRUN"] = "1"
    try:
        report = panel.run_panel("quest", controls_only=True, panel_size=3, write_db=False)
    finally:
        del os.environ["WORLDOS_ARTIFACT_PANEL_DRYRUN"]
    assert report["stale_bands"] == []
    assert report["panel_valid"] is True
