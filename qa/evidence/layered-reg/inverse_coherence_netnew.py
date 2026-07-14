#!/usr/bin/env python3
"""inverse_coherence_netnew.py — ADDITIONS-LOCK enforcement for the layered chain.

Runs the #1540 inverse-coherence painted-object detector on the registered BASE and on each layered
pass output, using the tavern_fit2 derived manifest. NET-NEW flags = cells flagged on a pass that are
NOT flagged on the base (i.e. furniture the layered treatment INVENTED on authored-clear floor).
Per #1556 calibration the adopt bar is net-new == 0.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, "qa")
from journey_visual_sweep import inverse_coherence_flags, load_plate_edges  # noqa: E402

LR = Path("qa/evidence/layered-reg")
manifest = json.load(open(LR / "tavern_fit2.cells.json"))
cols, rows = manifest["grid"]["cols"], manifest["grid"]["rows"]
walkable = manifest["walkable"]
prop_cells = {(int(c), int(r)) for p in manifest.get("props", []) for (c, r) in p.get("footprint", [])}

targets = {
    "base_fit2":            LR / "gen_base/base_fit2.png",
    "singlepass_s1111":     LR / "gen_base/fit2_singlepass_s1111.png",
    "singlepass_s1000":     LR / "gen_base/fit2_singlepass_s1000.png",
    "layered_p1_material":  LR / "passes/p1_material_s1111.png",
    "layered_p2_lighting":  LR / "passes/p2_lighting_s2111.png",
    "layered_p3_final":     LR / "passes/p3_scatter_s3000.png",
}

results = {}
flagsets = {}
for name, path in targets.items():
    ic = inverse_coherence_flags(load_plate_edges(str(path)), walkable, prop_cells, cols, rows, name)
    fset = {tuple(f["cell"]) for f in ic.flagged}
    flagsets[name] = fset
    results[name] = {"n_flagged": len(ic.flagged),
                     "flagged_cells": [f["cell"] for f in ic.flagged],
                     "threshold": round(ic.threshold, 4),
                     "baseline_median": round(ic.baseline_median, 4)}

base_flags = flagsets["base_fit2"]
out = {"n_walkable_floor": len([1 for (c, r) in walkable if (int(c), int(r)) not in prop_cells]),
       "base_flagged_cells": sorted([list(c) for c in base_flags]),
       "per_target": {}}
for name, fset in flagsets.items():
    net_new = sorted([list(c) for c in (fset - base_flags)])
    out["per_target"][name] = {**results[name], "net_new_vs_base": net_new,
                               "n_net_new": len(net_new)}

(LR / "inverse_coherence_netnew.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
