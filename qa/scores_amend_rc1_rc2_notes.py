#!/usr/bin/env python3
"""One-shot ledger amendment (2026-06-10): correct two FALSE claims in qa/scores.db notes.

Found by the post-Sprint-1 adversarial audit; evidence verified before amending:

1. ``v1.0.4-rc2-c92a393`` claimed "Same ruler sc_5ac7a1d9103c as rc1 (apples-to-apples)".
   FALSE — the row itself is stamped ``sc_df34ecd02b4f``. Between the rc1 recording (ruler state
   a104f1e → sc_5ac7a1d9103c) and the rc2 recording, #739 changed ``qa/assert_behavioral.py`` —
   a SCORING_CONFIG_FILES member — so rc1→rc2 is CROSS-RULER. The LENS prose comparison is still
   informative, but the BEHAVIORAL-capped lens comparability is a re-baseline, not a trend.
   (This false-claim class is now rejected at write time by the add_run notes/stamp guard.)

2. ``v1.0.4-rc1-fa97b34`` claimed "ui_audit FAIL(axe)". FALSE — the VM log
   (worldos-qa-results-fa97b34/results/ui_audit.log) shows axe NEVER RAN: browser-driver-manager
   was not installed (silent WARN-skip, log lines 32-33). The actual ui_audit failures were the
   launcher ``play_reachable`` gate (no Resume/Continue CTA @1366+@1512) and merchant
   ``art_present`` (placeholders=4 > cap 2).

Idempotent: exact-substring replacement; re-running after amendment is a no-op. Re-renders the
markdown ledger afterwards.

Run:  python3 qa/scores_amend_rc1_rc2_notes.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scores_db import DB_PATH, render_markdown  # noqa: E402

# (run_id, old substring, corrected substring)
AMENDMENTS = [
    (
        "v1.0.4-rc2-c92a393",
        "Same ruler sc_5ac7a1d9103c as rc1 (apples-to-apples).",
        "CROSS-RULER vs rc1, NOT apples-to-apples: #739 changed assert_behavioral.py (a "
        "SCORING_CONFIG_FILES member) between recordings, so this row's ruler sc_df34ecd02b4f "
        "differs from rc1's (see rc1 row). LENS prose comparison still informative; the "
        "BEHAVIORAL-capped lens comparability is a re-baseline. [AMENDED 2026-06-10: original "
        "note falsely claimed same-ruler.]",
    ),
    (
        "v1.0.4-rc1-fa97b34",
        "ui_audit FAIL(axe).",
        "ui_audit FAIL(launcher play_reachable: no Resume/Continue CTA @1366+@1512; merchant "
        "art_present placeholders=4>2 — axe never ran: browser-driver-manager missing on VM, "
        "silent WARN-skip). [AMENDED 2026-06-10: original note misattributed the FAIL to axe.]",
    ),
]


def main() -> int:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        changed = 0
        for run_id, old, new in AMENDMENTS:
            row = conn.execute("SELECT notes FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                print(f"SKIP {run_id}: row not found")
                continue
            notes = row[0] or ""
            if old in notes:
                conn.execute("UPDATE runs SET notes = ? WHERE run_id = ?",
                             (notes.replace(old, new), run_id))
                changed += 1
                print(f"AMENDED {run_id}")
            elif new in notes:
                print(f"OK {run_id}: already amended (no-op)")
            else:
                print(f"WARN {run_id}: neither original nor amended text found — manual review needed")
                return 1
        conn.commit()
    finally:
        conn.close()
    render_markdown()
    print(f"re-rendered ledger ({changed} row(s) amended)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
