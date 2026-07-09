#!/usr/bin/env python3
"""build_room_manifest.py — seed the DURABLE, VERSIONED per-room cell->bbox manifests (W6.3, #1462).

Emits qa/room_manifests/<room>.cells.json: for every authored prop, its logical cells reprojected to
the expected `screen_bbox` under the CONTRACT camera (greybox_render_headless.py's verified rig — the
#1396 reprojection recipe), plus (when a known-good plate is supplied) a per-prop reference FINGERPRINT
so qa/check_plate_drift.py is self-contained. This is the versioned successor to the one-off paint-drift
incident folders (qa/evidence/1397, 1408).

The authored prop layout is read straight from the scene_grid seeds (their module-level cell constants
are the single source of truth shared with the engine's impassable set), so a manifest is regeneratable
on demand and can never silently diverge from the seed:
  * camp_clearing_night_v2  <- qa/seed_gfx_camp.py            (16x12; the rest-camp fixture)
  * crypt_dense_v1          <- qa/seed_gfx_crypt_2room.py     (14x11; the TOMB unit — sarcophagus+pillars)

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
import seed_gfx_crypt_2room as crypt  # noqa: E402
from check_plate_drift import FP_GRID, fingerprint, load_luma, project_cell_bbox  # noqa: E402

_MANIFESTS_DIR = _QA_DIR / "room_manifests"
_CAMERA = {  # the contract greybox rig, recorded so a manifest is self-describing
    "recipe": "greybox_render_headless (verified vs Unity Quaternion.Euler(30,45,0) <1e-3)",
    "ortho_size": 13.0, "pitch_deg": 30.0, "yaw_deg": 45.0,
    "px_w": 1344, "px_h": 768, "cell_world_units": 2.0, "cam_dist": 80.0,
}


def _camp_props() -> list:
    """The camp_clearing_night prop decomposition — VERBATIM from seed_gfx_camp.py's authored grid
    (each tree pair, each bedroll, the log, etc. is its own prop, matching _build_camp_grid)."""
    t = camp.TREE_CELLS
    return [
        ("tree_0", "large_tree", [t[0], t[1]]),
        ("tree_1", "large_tree", [t[2], t[3]]),
        ("tree_2", "large_tree", [t[4], t[5]]),
        ("tree_3", "large_tree", [t[6], t[7]]),
        ("rock_l", "boulder", list(camp.ROCK_L_CELLS)),
        ("rock_r", "boulder", list(camp.ROCK_R_CELLS)),
        ("campfire", "campfire_pit", list(camp.CAMPFIRE_CELLS)),
        ("bedroll_1", "bedroll", [camp.BEDROLL_CELLS[0]]),
        ("bedroll_2", "bedroll", [camp.BEDROLL_CELLS[1]]),
        ("bedroll_3", "bedroll", [camp.BEDROLL_CELLS[2]]),
        ("log_seat", "fallen_log", list(camp.LOG_SEAT_CELLS)),
        ("supply_crates", "supply_crates", list(camp.SUPPLY_CRATE_CELLS)),
    ]


def _crypt_tomb_props() -> list:
    """The crypt_dense_v1 (TOMB unit) props — from seed_gfx_crypt_2room.py's TOMB_PROPS spec
    (id, kind, footprint, band, silhouette)."""
    return [(pid, kind, [list(c) for c in footprint]) for (pid, kind, footprint, _band, _sil)
            in crypt.TOMB_PROPS]


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
    for (pid, kind, cells) in props:
        cells = [[int(c), int(r)] for (c, r) in cells]
        bbox = [round(v, 2) for v in project_cell_bbox(cells, cols, rows)]
        entry = {"id": pid, "kind": kind, "cells": cells, "screen_bbox": bbox}
        if plate_arr is not None:
            fp = fingerprint(plate_arr, bbox)
            entry["ref_fp"] = [round(float(x), 5) for x in fp.tolist()]
        prop_entries.append(entry)

    return {
        "manifest_version": 1,
        "room": room,
        "recipe_key": recipe_key,
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
            source_seed="qa/seed_gfx_crypt_2room.py", cols=crypt.GRID_W, rows=crypt.GRID_H,
            props=_crypt_tomb_props(),
            source_plate=None),  # crypt_dense_v1.png lives on the box; geometry-only + fingerprint-ready
    }


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
            if current != text:
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
