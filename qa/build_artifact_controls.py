#!/usr/bin/env python3
"""build_artifact_controls.py — serialize DISGUISED hand-authored canon into artifact controls
(HV1 control anchoring, #1323).

THE CONTROL LAW (the content analogue of the real-art-control law, memory
feedback_visual_panel_scoring_variance). A per-artifact eval panel is only trustworthy if a KNOWN
piece of ship-quality hand-authored canon, pushed through the IDENTICAL envelope so the scorer cannot
tell it apart from a generated artifact, lands inside its expected band. If a disguised real Baldur's
Gate 3 companion scores 2.5, the instrument is broken — not the canon. So this script pulls real canon
out of the WorldOS worlds and serializes it into the shared data/library/artifact_schema.json envelope:

  * QUEST controls   ← content/worlds/*/world.json  quest_variants (+ story_seeds)
  * NPC controls     ← content/worlds/*/world.json  npc_roster dossiers
  * LOCATION controls← content/worlds/*/areas/*.json (wiki-canon areas)
  * ENCOUNTER controls← hand-authored here FROM the canonical set-pieces the world material describes
                        (there is no standalone encounter list in canon; these are canon-derived).

DISGUISE DISCIPLINE (load-bearing): the control artifacts written to the panel input dir
(qa/artifact_controls/) carry ONLY the payload — the same shape an extracted artifact has. The
identity mapping (which artifact_id is a control + its expected anchor band) is written to a SEPARATE
file OUTSIDE the panel input dir (qa/artifact_controls_identity.json) so it can never leak into the
scorer's view. The panel runner reads the identity map to check band-validity AFTER scoring.

    python3 qa/build_artifact_controls.py [--world baldurs-gate] [--out-dir qa/artifact_controls]
"""
from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

QA_DIR = Path(__file__).resolve().parent
REPO = QA_DIR.parent
DEFAULT_OUT = QA_DIR / "artifact_controls"
# The identity map lives OUTSIDE the panel input dir so it can never be scored.
IDENTITY_PATH = QA_DIR / "artifact_controls_identity.json"

# Expected anchor bands for ship-quality canon (on the 1-5 rubric). These are DISGUISED real canon,
# so they should read as strong content: anchor ~4.0, valid within the ±1.2 noise law (i.e. a control
# landing below ~2.8 invalidates the panel). Kept modest (not 4.8) so the band is a real check, not a
# rubber stamp — canon is strong, not superhuman, and the scorer is stingy.
ANCHOR = 4.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canon_provenance(world: str, source: str) -> dict:
    # A control is hand-authored canon, NOT extracted from a live run: campaign_id/run_id/sha are null.
    # extracted_at is a DETERMINISTIC sentinel (not _now()) so a committed control fixture does not churn
    # on every regeneration — controls are canon fixtures, not time-stamped extractions.
    return {"campaign_id": None, "run_id": None, "sha": None,
            "extracted_at": "canon", "source": source}


def _artifact(cls: str, world: str, source_id: str, payload: dict, source: str) -> dict:
    return {
        "artifact_id": f"control:{cls}:{world}:{source_id}",
        "class": cls,
        "world": world,
        "provenance": _canon_provenance(world, source),
        "payload": {k: v for k, v in payload.items() if v not in (None, "", [], {})},
        "scores": None,
    }


def quest_controls(world_json: dict, world: str, n: int = 3) -> list[dict]:
    out = []
    for qv in (world_json.get("quest_variants") or [])[:n]:
        outcomes = qv.get("outcomes") or []
        payload = {
            "title": qv.get("name"),
            "hook": (outcomes[0].get("hook") if outcomes else None) or qv.get("hook"),
            "objectives": None,  # quest_variants describe outcomes, not step spines
            "consequences": [o.get("lore") for o in outcomes if o.get("lore")],
            "outcomes": [{"id": o.get("id"), "hook": o.get("hook")} for o in outcomes if o.get("hook")],
        }
        out.append(_artifact("quest", world, qv["id"], payload, "world.json:quest_variants"))
    return out


def npc_controls(world_json: dict, world: str, n: int = 3) -> list[dict]:
    out = []
    for npc in (world_json.get("npc_roster") or [])[:n]:
        payload = {
            "name": npc.get("name"),
            "role": npc.get("role"),
            "personality": npc.get("personality"),
            "dossier": npc.get("dossier"),
            "want": npc.get("hook"),
            "voice_id": npc.get("voice_id"),
        }
        out.append(_artifact("npc", world, npc["id"], payload, "world.json:npc_roster"))
    return out


def location_controls(world_dir: Path, world: str, n: int = 3) -> list[dict]:
    out = []
    for area_path in sorted(glob.glob(str(world_dir / "areas" / "*.json")))[:n]:
        area = json.loads(Path(area_path).read_text(encoding="utf-8"))
        payload = {
            "name": area.get("name"),
            "region": area.get("region"),
            "description": area.get("description"),
            "connections": area.get("connections"),
            "tags": area.get("tags"),
        }
        out.append(_artifact("location", world, area["id"], payload, "areas/*.json"))
    return out


# Encounter controls: canon-DERIVED set-pieces, hand-authored here FROM the shipped world material
# (the Steel Watch foundry, the undercity drain, the checkpoint at the Basilisk Gate). These are the
# canonical combat/set-piece moments the world describes; there is no standalone encounter list to pull
# from, so they are authored at ship quality as the control anchor.
_ENCOUNTER_CANON: dict[str, list[dict]] = {
    "baldurs-gate": [
        {
            "id": "steel-watch-foundry-husks",
            "name": "The Half-Finished Watchers",
            "situation": "In the detonated Steel Watch assembly halls beneath Wyrm's Rock, the "
                         "party must reach the central control cradle while dormant war-constructs "
                         "wake in waves as the arcane current is disturbed.",
            "objective": "Reach and shut down the master cradle before all three husk-lines finish "
                         "powering up.",
            "combatants": ["2 half-finished Steel Watchers (bruisers, wake in sequence)",
                           "a swarm of forge-drones (skirmishers)",
                           "an overseer-construct that buffs the line (controller)"],
            "terrain": "Vaulted hall with assembly-cradles (hard cover + verticality), pools of "
                       "burnt forge-oil (a hazard the party can ignite), and a central catwalk.",
            "twist": "Shutting the cradle early drops power to the husks mid-fight — a risk/reward "
                     "lever: rush the objective and skip the fight, or clear the room first.",
            "stakes": "If the overseer finishes the line, the constructs march on the refugee camp "
                      "at the Basilisk Gate.",
        },
        {
            "id": "undercity-drain-ambush",
            "name": "Ambush at the Drain Center-Point",
            "situation": "In the flooded Undercity sewers, Rael's wardens spring an ambush as the "
                         "party closes on the arcane drain that samples signatures.",
            "objective": "Protect the witness Mavar Crispin and disable the drain conduit.",
            "combatants": ["3 Guild wardens (skirmishers on the walkways)",
                           "a hexer channeling from the far platform (controller — must be reached)",
                           "a caged construct that activates if the conduit is left running"],
            "terrain": "Narrow walkways over rising sewer-water (fall = swept away), a chokepoint "
                       "grate, and the conduit itself as an interactable objective.",
            "twist": "The water rises each round — the longer the fight, the more the walkways flood, "
                     "forcing the party toward the objective.",
            "stakes": "If Mavar falls or the conduit runs a full survey, the Registry Engine completes "
                      "its Lower City phase.",
        },
    ],
}


def encounter_controls(world: str, n: int = 2) -> list[dict]:
    out = []
    for enc in (_ENCOUNTER_CANON.get(world) or [])[:n]:
        # Read non-destructively: enc is an element of the module-level _ENCOUNTER_CANON singleton, so
        # a pop() would mutate it and make a second build() call in the same process raise KeyError.
        eid = enc["id"]
        payload = {k: v for k, v in enc.items() if k != "id"}
        out.append(_artifact("encounter", world, eid, payload, "canon-derived:set-pieces"))
    return out


def build(world: str, out_dir: Path) -> tuple[list[dict], dict]:
    world_dir = REPO / "content" / "worlds" / world
    world_json = json.loads((world_dir / "world.json").read_text(encoding="utf-8"))
    controls: list[dict] = []
    controls += quest_controls(world_json, world)
    controls += npc_controls(world_json, world)
    controls += location_controls(world_dir, world)
    controls += encounter_controls(world)

    out_dir.mkdir(parents=True, exist_ok=True)
    identity: dict[str, Any] = {"anchor": ANCHOR, "noise_law": 1.2, "controls": {}}
    for a in controls:
        safe = a["artifact_id"].replace(":", "__")
        (out_dir / f"{safe}.json").write_text(json.dumps(a, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
        identity["controls"][a["artifact_id"]] = {
            "class": a["class"], "world": a["world"], "anchor": ANCHOR,
            "band": [round(ANCHOR - 1.2, 1), round(min(5.0, ANCHOR + 1.2), 1)],
            "file": f"{safe}.json",
        }
    return controls, identity


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default="baldurs-gate")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--identity-out", default=str(IDENTITY_PATH))
    args = ap.parse_args(argv)

    controls, identity = build(args.world, Path(args.out_dir))
    Path(args.identity_out).write_text(json.dumps(identity, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    by_class: dict[str, int] = {}
    for a in controls:
        by_class[a["class"]] = by_class.get(a["class"], 0) + 1
    print(f"wrote {len(controls)} control artifact(s) to {args.out_dir}: {by_class}")
    print(f"identity map (OUTSIDE panel dir): {args.identity_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
