"""Red-first coverage for the Agent G4 surface and row CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))
import scores_db  # noqa: E402

def test_agent_g4_surface_is_accepted(tmp_path):
    db = tmp_path / "scores.db"
    scores_db.add_run("g4", db_path=db, surface="agent_g4")
    assert scores_db.fetch_rows(db)[0]["surface"] == "agent_g4"

def test_unknown_surface_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="not-a-surface"):
        scores_db.add_run("g4", db_path=tmp_path / "scores.db", surface="not-a-surface")

def test_agent_g4_cli_row_shape(tmp_path):
    db, frames = tmp_path / "scores.db", tmp_path / "frames"
    frames.mkdir()
    result = subprocess.run([
        sys.executable, str(QA_DIR / "agent_g4_row.py"), "--build", "bf890b43",
        "--p1", "5", "--p2", "13", "--p3", "1", "--route-complete", "1",
        "--pass1", "FAIL", "--pass2", "FAIL", "--frames-dir", str(frames),
        "--persist", "--run-id", "agent_g4_test", "--db", str(db),
    ], check=True, capture_output=True, text=True)
    assert "agent_g4_test" in result.stdout
    row = scores_db.fetch_rows(db)[0]
    keys = ("surface", "build_sha", "p1_count", "p2_count", "p3_count", "route_completion",
            "pass1_verdict", "pass2_verdict", "pass", "source_path")
    assert tuple(row[k] for k in keys) == ("agent_g4", "bf890b43", 5, 13, 1, 1,
                                            "FAIL", "FAIL", 0, str(frames))
    assert row["methodology"].startswith("agent-g4 pass=both build=bf890b43 ")
