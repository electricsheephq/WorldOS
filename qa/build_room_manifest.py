#!/usr/bin/env python3
"""build_room_manifest.py — seed the DURABLE, VERSIONED per-room cell->bbox manifests (W6.3, #1462).

Emits qa/room_manifests/<room>.cells.json: for every authored prop, its FOOTPRINT + OCCLUSION cells and
the footprint reprojected to the expected `screen_bbox` under the CONTRACT camera (greybox_render_headless.py's
verified rig — the #1396 reprojection recipe), plus (when a known-good plate is supplied) a per-prop
reference FINGERPRINT so qa/check_plate_drift.py is self-contained. Versioned successor to the one-off
paint-drift incident folders (qa/evidence/1397, 1408).

Per-prop the manifest carries BOTH (they diverge under the iso projection — #1505):
  * footprint — the impassable FLOOR cells (what collision + qa/check_grid_paint_coherence.py check).
  * occlusion — the screen-space SILHOUETTE cells (what occluder proxies / silhouette rendering use).
`cells` + `screen_bbox` mirror the footprint (drift-gate back-compat).

The authored prop layout is read straight from the scene_grid seeds (their module-level cell constants
are the single source of truth shared with the engine's impassable set), so a manifest is regeneratable
on demand and can never silently diverge from the seed:
  * camp_clearing_night_v2  <- qa/seed_gfx_camp.py     (16x12; the rest-camp fixture; occlusion==footprint)
  * crypt_dense_v1          <- MEASURED (PR #1507 owner-playtest-#5 point-in-polygon re-fit; 14x11 deployed
                              crypt_armb_iter3_v1 grid) — a stable measured snapshot whose footprints now
                              AGREE with seed_gfx_combat.py (values copied in, not imported).

  python3 qa/build_room_manifest.py          # regenerate both committed manifests
  python3 qa/build_room_manifest.py --check   # verify the committed manifests match the seeds (CI-safe)

Read-only w.r.t. engine state; writes only the manifest JSONs under qa/room_manifests/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import seed_gfx_camp as camp  # noqa: E402
from check_plate_drift import FP_GRID, fingerprint, load_luma, project_cell_bbox  # noqa: E402

# The DEPLOYED crypt (crypt_armb_iter3_v1 plate) grid values, MEASURED — a stable self-describing
# SNAPSHOT (kept decoupled from the concurrently-edited seed_gfx_combat.py). Values are the OWNER
# PLAYTEST #5 collision-coherence re-measurement (PR #1507): #1505's copied cells (pillar (2,4)/(9,9),
# sarcophagus cols2-7 x rows7-9) were still DRIFTED off the paint — the coherence gate flagged the crypt
# INCOHERENT (pillar_l 0.79 off, pillar_r/sarcophagus unlocated). Re-derived by projecting the grid onto
# the deployed plate + point-in-polygon of each floor polygon; these are the newer truth and match
# seed_gfx_combat.py's footprints (manifest + fixture now agree).
_CRYPT_GRID = (14, 11)
_CRYPT_PILLAR_L = [[3, 3], [3, 4]]
_CRYPT_PILLAR_R = [[8, 9], [9, 9]]
_CRYPT_SARCOPHAGUS_FOOTPRINT = [  # the coffin BODY floor cells, cols3-7 x rows6-8 (irregular, 12)
    [4, 6], [5, 6], [6, 6], [7, 6],
    [3, 7], [4, 7], [5, 7], [6, 7], [7, 7],
    [4, 8], [5, 8], [6, 8],
]

_MANIFESTS_DIR = _QA_DIR / "room_manifests"
_CAMERA = {  # the contract greybox rig, recorded so a manifest is self-describing
    "recipe": "greybox_render_headless (verified vs Unity Quaternion.Euler(30,45,0) <1e-3)",
    "ortho_size": 13.0, "pitch_deg": 30.0, "yaw_deg": 45.0,
    "px_w": 1344, "px_h": 768, "cell_world_units": 2.0, "cam_dist": 80.0,
}


# A prop entry is (id, kind, footprint, occlusion):
#   footprint  = the impassable FLOOR cells (what collision + the coherence gate check).
#   occlusion  = the screen-space SILHOUETTE cells (what occluder proxies / silhouette rendering use).
# Under the iso projection a tall prop's silhouette rises UP-SCREEN off its floor footprint (the
# sarcophagus is the canonical case — see #1505). When no distinct silhouette is measured (thin/short
# props, outdoor scatter), occlusion defaults to the footprint.
def _camp_props() -> list:
    """The camp_clearing_night_v2 prop decomposition — VERBATIM from seed_gfx_camp.py's authored grid
    (fire pit, firewood, crates, stone walls, gate posts, lean-to, bedrolls), matching _build_camp_grid's
    prop list one-for-one (owner playtest #5 re-measurement of the DEPLOYED v2 plate). These solids have
    no measured up-screen silhouette split, so occlusion == footprint."""
    fp = [
        ("campfire", "campfire_pit", list(camp.CAMPFIRE_CELLS)),
        ("firewood", "fallen_log", list(camp.FIREWOOD_CELLS)),
        ("crate_l", "supply_crates", list(camp.CRATE_L_CELLS)),
        ("crate_c", "supply_crates", list(camp.CRATE_C_CELLS)),
        ("crate_wall", "supply_crates", list(camp.CRATE_WALL_CELLS)),
        ("crate_r", "supply_crates", list(camp.CRATE_R_CELLS)),
        ("wall_bl", "stone_wall", list(camp.WALL_BL_CELLS)),
        ("wall_br", "stone_wall", list(camp.WALL_BR_CELLS)),
        ("post_l", "stone_pillar", list(camp.POST_CELLS)),
        ("shelter", "timber_frame", list(camp.SHELTER_CELLS)),
        ("bedroll_l", "bedroll", list(camp.BEDROLL_L_CELLS)),
        ("bedroll_r", "bedroll", list(camp.BEDROLL_R_CELLS)),
    ]
    return [(pid, kind, [list(c) for c in cells], [list(c) for c in cells]) for (pid, kind, cells) in fp]


# The sarcophagus SILHOUETTE (occlusion) — the coffin's tall lid+effigy rise up-screen to cols3-9 x
# rows3-7 under the iso projection. This is the #1386 value #1505 correctly RECLASSIFIED as the
# silhouette (NOT the impassable footprint), point-in-polygon-verified on the deployed plate.
_SARCOPHAGUS_OCCLUSION = [[c, r] for c in range(3, 10) for r in range(3, 8)]


def _crypt_tomb_props() -> list:
    """The DEPLOYED crypt (crypt_armb_iter3_v1 plate) props — MEASURED from PR #1507's owner-playtest-#5
    collision-coherence re-measurement (supersedes #1505, which still read INCOHERENT on the gate). The
    sarcophagus carries its distinct up-screen silhouette as occlusion; the pillars' tall-but-thin
    silhouette has no separate measured extent, so occlusion == footprint."""
    return [
        ("pillar_l", "stone_pillar", _CRYPT_PILLAR_L, _CRYPT_PILLAR_L),
        ("pillar_r", "stone_pillar", _CRYPT_PILLAR_R, _CRYPT_PILLAR_R),
        ("sarcophagus", "sarcophagus", _CRYPT_SARCOPHAGUS_FOOTPRINT, _SARCOPHAGUS_OCCLUSION),
    ]


def build_manifest(*, room: str, recipe_key: str, source_seed: str, cols: int, rows: int,
                   props: list, source_plate: str | None = None) -> dict:
    """Assemble one room manifest. If `source_plate` resolves to a committed known-good plate, embed a
    per-prop reference fingerprint (self-contained gate); otherwise ship geometry only (crypt has no
    committed plate yet — the manifest is still the durable cell->bbox artifact + fingerprint-ready)."""
    plate_path = None
    if source_plate:
        p = _QA_DIR / source_plate if not Path(source_plate).is_absolute() else Path(source_plate)
        plate_path = p if p.is_file() else None
    plate_arr = load_luma(plate_path) if plate_path is not None else None

    prop_entries = []
    for (pid, kind, footprint, occlusion) in props:
        footprint = [[int(c), int(r)] for (c, r) in footprint]
        occlusion = [[int(c), int(r)] for (c, r) in occlusion]
        # screen_bbox + the drift-gate `cells` are keyed to the FOOTPRINT (the floor cells collision +
        # the coherence gate check), NOT the up-screen silhouette.
        bbox = [round(v, 2) for v in project_cell_bbox(footprint, cols, rows)]
        entry = {"id": pid, "kind": kind, "footprint": footprint, "occlusion": occlusion,
                 "cells": footprint, "screen_bbox": bbox}
        if plate_arr is not None:
            fp = fingerprint(plate_arr, bbox)
            entry["ref_fp"] = [round(float(x), 5) for x in fp.tolist()]
        prop_entries.append(entry)

    return {
        "manifest_version": 1,
        "room": room,
        "recipe_key": recipe_key,
        # MEASURED, not geometry-DERIVED: crypt/camp have no greybox geometry JSON yet, so their
        # footprint+occlusion are reconstructed from measured calibrations (crypt: PR #1505's point-in-
        # polygon tomb footprint; camp: the W6.2-era authored grid). Geometry-json derivation via
        # tools/derive_room_manifest.py (the forest_road path) is the follow-up — owner playtest #5.
        "derivation": "measured",
        "source_seed": source_seed,
        "source_plate": (str(plate_path.relative_to(_QA_DIR.parent)) if plate_path else None),
        "grid": {"cols": cols, "rows": rows},
        "camera": _CAMERA,
        "fingerprint": {"grid": FP_GRID, "metric": "mean-sub L2-normalised luma NCC"},
        "props": prop_entries,
    }


def _manifests() -> dict:
    return {
        "camp_clearing_night_v2": build_manifest(
            room="camp_clearing_night_v2", recipe_key="camp_clearing_night",
            source_seed="qa/seed_gfx_camp.py", cols=camp.GRID_W, rows=camp.GRID_H,
            props=_camp_props(),
            source_plate="evidence/plate-audit/camp_clearing_night_v2.jpg"),
        "crypt_dense_v1": build_manifest(
            room="crypt_dense_v1", recipe_key="crypt",
            source_seed="measured: PR #1507 owner-playtest-#5 point-in-polygon re-fit",
            cols=_CRYPT_GRID[0], rows=_CRYPT_GRID[1],
            props=_crypt_tomb_props(),
            source_plate=None),  # crypt_armb_iter3_v1.png lives on the box; geometry-only + fingerprint-ready
    }


def _strip_ref_fp(manifest: dict) -> dict:
    """A copy of the manifest with every prop's `ref_fp` removed — the ONLY env-sensitive field
    (a luma NCC fingerprint whose last digits wobble across pillow/numpy versions). Everything else
    (footprint/occlusion cells, bboxes, grid, camera) is exact integer/geometry data."""
    out = json.loads(json.dumps(manifest))  # deep copy
    for p in out.get("props", []):
        p.pop("ref_fp", None)
    return out


def manifests_equivalent(committed_text: str, fresh_text: str, *, fp_atol: float = 5e-2) -> bool:
    """Staleness compare that is TOLERANT of the fingerprint floats. The per-prop `ref_fp` NCC vectors
    are regenerated from a JPEG decode, so their trailing digits differ across libjpeg builds — notably
    macOS (a dev box) vs Linux (CI) — a byte-exact compare here is a known CI-flake trap (env-pinned
    golden floats). The TRUE staleness guard is the structural compare: every geometry field (`footprint`,
    `occlusion`, `cells`, `screen_bbox`, `grid`, `camera`) must match EXACTLY, so any seed cell edit is
    caught there. `ref_fp` need only agree within `fp_atol` — generous vs cross-platform JPEG-decode noise
    (~1e-2) yet ~20x tighter than a real prop MOVE/REPAINT (NCC delta ~0.3-1.0), already flagged above."""
    try:
        committed = json.loads(committed_text)
        fresh = json.loads(fresh_text)
    except (json.JSONDecodeError, ValueError):
        return False
    if _strip_ref_fp(committed) != _strip_ref_fp(fresh):
        return False
    for pc, pf in zip(committed.get("props", []), fresh.get("props", [])):
        fc, ff = pc.get("ref_fp"), pf.get("ref_fp")
        if (fc is None) != (ff is None):
            return False
        if fc is not None:
            if len(fc) != len(ff) or any(abs(a - b) > fp_atol for a, b in zip(fc, ff)):
                return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="verify committed manifests match the seeds (exit 1 on drift); write nothing")
    args = ap.parse_args(argv)
    _MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    rc = 0
    for name, manifest in _manifests().items():
        path = _MANIFESTS_DIR / f"{name}.cells.json"
        text = json.dumps(manifest, indent=1) + "\n"
        if args.check:
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
            if not manifests_equivalent(current, text):
                print(f"[build_room_manifest] STALE: {path.name} does not match the seed — "
                      f"re-run `python3 qa/build_room_manifest.py`", file=sys.stderr)
                rc = 1
            else:
                print(f"[build_room_manifest] OK: {path.name} matches the seed")
        else:
            path.write_text(text, encoding="utf-8")
            fp = "with fingerprints" if manifest["props"] and "ref_fp" in manifest["props"][0] \
                else "geometry only"
            print(f"[build_room_manifest] wrote {path.name} "
                  f"({len(manifest['props'])} props, {fp})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
