#!/usr/bin/env python3
"""Select the best flux draw of a room-paint cycle by structural edge-recall.

The variance absorber of the registered room pipeline (docs/roadmap/PROCEDURAL-SCORECARD.md):
every paint cycle generates N draws (numOutputs / repeated seeds) and the SELECTION GATE picks the
draw whose edges best match the conditioning structure. Single-shot draws shipped 2/2 failures on
the tavern and mis-attributed a style collapse on the crypt (2026-07-15) — selection is mandatory
for every room class.

Reference image choice (measured 2026-07-15): the UNITY greybox render is texture-dense (Perlin
stone) and caps recall ~0.2 regardless of alignment — pass the DEPTH render (--reference), whose
edges are purely structural. Absolute recall vs depth is NOT comparable to the recipe's 0.95
greybox bar; this tool's contract is RANKING (winner = max recall), with the absolute value
recorded for drift watching.

Usage:
  python3 qa/select_best_draw.py --reference /tmp/room/depth.png draw1.png draw2.png draw3.png
  → prints a recall table + WINNER line; exits 0 with the winner path on the last line.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plate_overlays import registration_recall  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", required=True,
                    help="structural reference image (the room DEPTH render; smooth, structural edges only)")
    ap.add_argument("candidates", nargs="+", help="candidate draw PNGs (>=2 for a real selection)")
    ap.add_argument("--json-out", help="optional path to write the selection record as JSON")
    args = ap.parse_args()

    ref = Path(args.reference)
    if not ref.is_file():
        ap.error(f"reference not found: {ref}")
    rows = []
    for cand in args.candidates:
        p = Path(cand)
        if not p.is_file():
            ap.error(f"candidate not found: {p}")
        rows.append({"candidate": str(p), "recall": round(registration_recall(ref, p), 4)})

    rows.sort(key=lambda r: r["recall"], reverse=True)
    for r in rows:
        print(f"{r['recall']:.4f}  {r['candidate']}")
    winner = rows[0]
    record = {"reference": str(ref), "rows": rows, "winner": winner["candidate"],
              "note": "ranking contract; absolute recall vs depth is not the 0.95 greybox bar"}
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(record, indent=1))
    if len(rows) < 2:
        print("WARNING: single candidate — no selection happened (the gate needs >=2 draws)", file=sys.stderr)
    print(f"WINNER {winner['recall']:.4f} {winner['candidate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
