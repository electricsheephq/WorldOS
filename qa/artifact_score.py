#!/usr/bin/env python3
"""artifact_score.py — score ONE content artifact (quest / npc / location / encounter) against its
class rubric, the harvest loop's per-artifact eval instrument (HV1, #1323).

WHAT IT IS
----------
The engine-duo lenses (qa/score.sh + rubric_tolkien/rubric/rubric_angry_dm) grade a whole PLAYTEST.
The harvest loop needs to grade a single reusable CONTENT artifact in isolation — is THIS quest / NPC
/ location / encounter a harvestable asset? This module is that instrument. It:

  1. loads an artifact JSON conforming to data/library/artifact_schema.json (the shared HV1/HV2 envelope),
  2. serializes its per-class payload into a plain-text "artifact card" (the thing the scorer reads),
  3. invokes qa/score.sh — REUSING its scorer-model pinning + isolated-config + keychain-auth
     discipline verbatim (we do NOT fork the auth logic) — with the class rubric + plain-number schema,
  4. parses the resulting scorecard and, unless --no-db, appends ONE row to the additive `artifacts`
     table in qa/scores.db via scores_db.add_artifact (its sole writer).

WHY REUSE score.sh (not a fresh claude -p): score.sh already solved the hard, load-bearing problems —
scorer-model pinning (sonnet, the gate baseline), the E2BIG stdin pipe, the fresh CLAUDE_CONFIG_DIR +
keychain-derived OAuth token that makes the scorer immune to the host's z.ai/GLM routing config, the
429 quota sentinel, and the timeout guard. Forking any of that would silently drift the instrument off
the canonical scorer. score.sh is generic over <transcript.md> <state.json> <rubric.md> <schema.json>
<out.json>, so the artifact card is the <transcript> and a tiny provenance blob is the <state>.

CLI
---
    python3 qa/artifact_score.py <artifact.json> [--panel-id ID] [--out OUT.json] [--no-db]
                                 [--budget 1.50] [--db qa/scores.db]

Read-only over game/world state. Writes only the <out> scorecard (temp by default) and (unless
--no-db) one row in the `artifacts` table.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
from scoring_config_version import artifact_config_version  # noqa: E402

SCORE_SH = QA_DIR / "score.sh"

# Per-class rubric + schema pairing (the ARTIFACT ruler files). Keep in sync with
# scoring_config_version.ARTIFACT_CONFIG_FILES — the same 8 files.
RUBRIC_FOR_CLASS: dict[str, tuple[str, str]] = {
    "quest": ("rubric_artifact_quest.md", "score_schema_artifact_quest.json"),
    "npc": ("rubric_artifact_npc.md", "score_schema_artifact_npc.json"),
    "location": ("rubric_artifact_location.md", "score_schema_artifact_location.json"),
    "encounter": ("rubric_artifact_encounter.md", "score_schema_artifact_encounter.json"),
}

# The CANONICAL per-class payload required fields (data/library/artifact_schema.json definitions,
# authored by HV2 #1329). The envelope's `payload` is currently an open object — the per-class
# definitions are NOT yet bound to `class` via an if/then (flagged on #1329; HV3 will bind them). Until
# then, artifact_score VALIDATES the class-payload shape EXPLICITLY here, so a malformed / mis-classed
# payload fails loudly at load time instead of being silently mis-scored.
_CANONICAL_PAYLOAD_REQUIRED: dict[str, tuple[str, ...]] = {
    "quest": ("id", "name", "objectives", "completed_objectives", "resolution_status",
              "evolves_to", "consequences"),
    "npc": ("id", "name", "voice_id", "personality", "attitude_arc", "final_status",
            "dialogue_snippets"),
    "location": ("id", "name", "description", "scene_grid", "visited"),
    "encounter": ("id", "composition", "outcome"),
}

# The per-class payload fields a rubric reads, in a stable display order. Any extra payload keys are
# appended after these so the card never silently drops context the extractor carried.
# Ordered to lead with the CANONICAL per-class payload field names (data/library/artifact_schema.json
# quest_payload / npc_payload / location_payload / encounter_payload, authored by HV2 #1329) so the card
# reads a real HV2-extracted artifact cleanly; the extra HV1-descriptive fields (hook / dossier / terrain
# / twist / stakes …) follow and are picked up when present (controls + richer artifacts carry them).
_CARD_FIELDS: dict[str, list[str]] = {
    "quest": ["name", "objectives", "completed_objectives", "resolution_status", "evolves_to",
              "consequences", "title", "hook", "giver", "stakes", "outcomes"],
    "npc": ["name", "voice_id", "personality", "attitude_arc", "final_status", "dialogue_snippets",
            "role", "dossier", "want", "hook"],
    "location": ["name", "description", "scene_grid", "visited", "region", "connections", "tags"],
    "encounter": ["composition", "outcome", "name", "situation", "objective", "combatants", "terrain",
                  "twist", "stakes"],
}


def validate_payload_shape(cls: str, payload: dict) -> None:
    """Explicitly validate a class-payload shape against the CANONICAL required fields.

    The shared envelope does not yet bind the per-class payload definitions to `class` via if/then
    (flagged on #1329; HV3 will). Until it does, this is the HV1-side guard the coordinator asked for:
    a payload missing its canonical required fields (or mis-classed) fails LOUDLY here instead of being
    silently mis-scored. Raises ValueError listing the missing fields."""
    required = _CANONICAL_PAYLOAD_REQUIRED.get(cls, ())
    missing = [f for f in required if f not in payload]
    if missing:
        raise ValueError(
            f"{cls} payload missing canonical required field(s) {missing}; "
            f"expected {list(required)} (data/library/artifact_schema.json {cls}_payload)"
        )


def load_artifact(path: Path, *, strict_payload: bool = True) -> dict:
    """Load + validate an artifact JSON against the shared envelope + its per-class payload shape.

    strict_payload (default True) enforces the canonical per-class required fields via
    validate_payload_shape — the explicit class-payload guard until the schema binds it (#1329/HV3)."""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"artifact {path} is not a JSON object")
    for req in ("artifact_id", "class", "payload"):
        if req not in obj:
            raise ValueError(f"artifact {path} missing required key {req!r}")
    if obj["class"] not in RUBRIC_FOR_CLASS:
        raise ValueError(f"artifact {path} class {obj['class']!r} not in {sorted(RUBRIC_FOR_CLASS)}")
    if not isinstance(obj.get("payload"), dict):
        raise ValueError(f"artifact {path} payload is not an object")
    if strict_payload:
        validate_payload_shape(obj["class"], obj["payload"])
    return obj


def _fmt_value(v: Any) -> str:
    """Render one payload value into the card as readable text (lists → bullets, scalars → str)."""
    if isinstance(v, list):
        parts = []
        for item in v:
            if isinstance(item, dict):
                parts.append("  - " + json.dumps(item, ensure_ascii=False))
            else:
                parts.append(f"  - {item}")
        return "\n".join(parts)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return str(v)


def build_card(artifact: dict) -> str:
    """Serialize an artifact's per-class payload into a plain-text card the scorer reads as the
    <transcript>. DISGUISE-SAFE: the card contains ONLY the payload content — never the provenance,
    the artifact_id, or any is-control marker — so a disguised canon control is indistinguishable
    from an extracted artifact of the same class (the panel-validity requirement)."""
    cls = artifact["class"]
    payload = artifact["payload"]
    ordered = _CARD_FIELDS.get(cls, [])
    lines: list[str] = [f"# {cls.upper()} ARTIFACT", ""]
    seen: set[str] = set()
    for field in ordered:
        if field in payload and payload[field] not in (None, "", [], {}):
            seen.add(field)
            lines.append(f"## {field}")
            lines.append(_fmt_value(payload[field]))
            lines.append("")
    # Append any remaining payload keys (extractor context) so nothing is silently dropped.
    for k, v in payload.items():
        if k not in seen and v not in (None, "", [], {}):
            lines.append(f"## {k}")
            lines.append(_fmt_value(v))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def score_artifact(
    artifact: dict,
    *,
    budget: str = "1.50",
    out_path: Optional[Path] = None,
    workdir: Optional[Path] = None,
) -> dict:
    """Score ONE loaded artifact via qa/score.sh; return the parsed scorecard dict.

    Raises RuntimeError if score.sh fails to produce a valid scorecard (a sentinel / non-zero exit) —
    the caller (the panel runner) treats that as an infra failure of the instrument, not a 0-score.
    """
    cls = artifact["class"]
    rubric_name, schema_name = RUBRIC_FOR_CLASS[cls]
    rubric = QA_DIR / rubric_name
    schema = QA_DIR / schema_name

    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="artifact-score-"))
    tmp.mkdir(parents=True, exist_ok=True)
    card_path = tmp / "card.md"
    # score.sh takes a <state.json> ground-truth arg; artifacts have none, so hand it a tiny
    # provenance-free stub (kept minimal + disguise-safe — no id, no control marker).
    state_path = tmp / "state.json"
    out = Path(out_path) if out_path else (tmp / "score.json")

    card_path.write_text(build_card(artifact), encoding="utf-8")
    state_path.write_text(json.dumps({"artifact_class": cls}) + "\n", encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCORE_SH), str(card_path), str(state_path), str(rubric),
         str(schema), str(out), budget],
        capture_output=True, text=True,
    )
    if not out.exists():
        raise RuntimeError(f"score.sh produced no output for {artifact['artifact_id']}: {proc.stderr[-500:]}")
    card = json.loads(out.read_text(encoding="utf-8"))
    if "scores" not in card or "overall" not in card:
        # sentinel ({error:scorer_failed} / {quota_exhausted}) — surface loudly.
        raise RuntimeError(
            f"score.sh returned a non-scorecard for {artifact['artifact_id']} "
            f"(rc={proc.returncode}): {json.dumps(card)[:300]}"
        )
    return card


def record_artifact_score(
    artifact: dict,
    card: dict,
    *,
    panel_id: Optional[str] = None,
    scorer_model: str = "sonnet",
    is_control: bool = False,
    control_anchor: Optional[float] = None,
    source_path: Optional[str] = None,
    notes: Optional[str] = None,
    db_path: Path | str = scores_db.DB_PATH,
) -> None:
    """Append ONE row to the `artifacts` table from a loaded artifact + its scorecard."""
    prov = artifact.get("provenance") or {}
    scores_db.add_artifact(
        artifact["artifact_id"],
        db_path=db_path,
        **{"class": artifact["class"]},
        run_id=prov.get("run_id"),
        world=artifact.get("world"),
        sha=prov.get("sha"),
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        dims_json=card.get("scores"),
        overall=card.get("overall"),
        panel_id=panel_id,
        scorer_model=scorer_model,
        is_control=int(bool(is_control)),
        control_anchor=control_anchor,
        source_path=source_path,
        notes=notes,
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("artifact", help="path to an artifact JSON (data/library/artifact_schema.json shape)")
    ap.add_argument("--panel-id", default=None, help="calibration-panel id grouping the N scorers")
    ap.add_argument("--out", default=None, help="write the scorecard JSON here (default: a temp file)")
    ap.add_argument("--no-db", action="store_true", help="do not append a row to the artifacts table")
    ap.add_argument("--is-control", action="store_true", help="mark this row as a disguised canon control")
    ap.add_argument("--control-anchor", type=float, default=None, help="expected anchor midpoint for a control")
    ap.add_argument("--budget", default="1.50", help="per-scorer USD budget passed to score.sh")
    ap.add_argument("--db", default=str(scores_db.DB_PATH), help="path to scores.db")
    args = ap.parse_args(argv)

    artifact = load_artifact(Path(args.artifact))
    card = score_artifact(artifact, budget=args.budget,
                          out_path=Path(args.out) if args.out else None)
    print(json.dumps({"artifact_id": artifact["artifact_id"], "class": artifact["class"],
                      "overall": card.get("overall"), "scores": card.get("scores"),
                      "ac_ruler": artifact_config_version()}, indent=2, ensure_ascii=False))
    if not args.no_db:
        record_artifact_score(
            artifact, card, panel_id=args.panel_id, is_control=args.is_control,
            control_anchor=args.control_anchor, source_path=args.artifact, db_path=args.db,
        )
        print(f"recorded artifact {artifact['artifact_id']} (overall={card.get('overall')})",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
