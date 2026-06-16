#!/usr/bin/env python3
"""Tests for qa/visual_regression_check.py — the GUI screenshot visual-regression signal.

Run (single-process; NEVER xdist):
    uv run --directory servers/engine python -m pytest qa/test_visual_regression_check.py -q -p no:xdist

These tests synthesize tiny PNG / byte files in ``tmp_path`` only — this is a pure reader;
it never writes any committed data artifact and never runs a game / scores a transcript.
"""

from __future__ import annotations

import io
import json
import struct
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import visual_regression_check as vrc  # noqa: E402

try:  # PIL is optional; AUDIT-mode tests adapt to its presence.
    from PIL import Image  # noqa: F401

    _HAVE_PIL = True
except Exception:  # pragma: no cover - depends on the host env
    _HAVE_PIL = False


# --------------------------------------------------------------------------------------
# Fixture helpers — synthesize tiny PNG bytes (PIL when present, otherwise a hand-rolled
# valid minimal PNG so strict-mode tests never depend on PIL).
# --------------------------------------------------------------------------------------
def _png_bytes_pil(w: int, h: int, color) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def _png_bytes_stdlib(w: int, h: int, byte_fill: int = 0) -> bytes:
    """A structurally-valid-enough PNG for hash/dimension purposes: real 8-byte signature +
    a real IHDR chunk (so dimension parsing works) followed by deterministic filler bytes.
    Strict mode only hashes bytes + reads IHDR, so this is sufficient and PIL-free."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_body = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit, truecolor RGB
    ihdr = struct.pack(">I", len(ihdr_body)) + b"IHDR" + ihdr_body + struct.pack(">I", 0)
    filler = bytes([byte_fill]) * 16
    return sig + ihdr + filler


def _make_png(w: int, h: int, color=(10, 20, 30), byte_fill: int = 0) -> bytes:
    if _HAVE_PIL:
        return _png_bytes_pil(w, h, color)
    return _png_bytes_stdlib(w, h, byte_fill)


def _layout(root: Path, files: dict[str, bytes]) -> Path:
    """Write ``{rel_path: bytes}`` under ``root`` (creating parent dirs). Returns ``root``."""
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


# --------------------------------------------------------------------------------------
# stdlib helpers
# --------------------------------------------------------------------------------------
def test_sha256_and_dims_are_pure_stdlib(tmp_path):
    png = _make_png(13, 7)
    f = tmp_path / "a.png"
    f.write_bytes(png)
    h = vrc.sha256_file(f)
    assert isinstance(h, str) and len(h) == 64
    # Same bytes -> same hash.
    assert vrc.sha256_file(f) == h
    dims = vrc.png_dimensions(png)
    assert dims == (13, 7)


def test_png_dimensions_returns_none_for_non_png():
    assert vrc.png_dimensions(b"not a png at all") is None
    assert vrc.png_dimensions(b"") is None


# --------------------------------------------------------------------------------------
# STRICT mode
# --------------------------------------------------------------------------------------
def test_strict_identical_is_pass(tmp_path):
    png = _make_png(8, 8)
    base = _layout(tmp_path / "base", {"newbie/table.png": png, "newbie/combat.png": png})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": png, "newbie/combat.png": png})
    res = vrc.compare(base, cand, mode="strict")
    assert res["mode"] == "strict"
    assert res["verdict"] == "PASS"
    assert res["counts"]["changed"] == 0
    assert res["counts"]["matched"] == 2
    assert res["counts"]["missing_candidate"] == 0
    assert res["counts"]["missing_baseline"] == 0
    # Every compared view is PASS with matching hashes.
    for v in res["views"]:
        assert v["status"] == "PASS"
        assert v["baseline_sha256"] == v["candidate_sha256"]


def test_strict_changed_byte_is_flag(tmp_path):
    png = _make_png(8, 8, color=(10, 20, 30), byte_fill=0)
    changed = _make_png(8, 8, color=(200, 10, 10), byte_fill=255)
    assert png != changed
    base = _layout(tmp_path / "base", {"newbie/table.png": png})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": changed})
    res = vrc.compare(base, cand, mode="strict")
    assert res["verdict"] == "FLAG"
    assert res["counts"]["changed"] == 1
    view = res["views"][0]
    assert view["status"] == "CHANGED"
    assert view["baseline_sha256"] != view["candidate_sha256"]


def test_strict_dimension_change_is_flag(tmp_path):
    a = _make_png(8, 8)
    b = _make_png(16, 8)  # same-ish content, different dimensions
    base = _layout(tmp_path / "base", {"p/table.png": a})
    cand = _layout(tmp_path / "cand", {"p/table.png": b})
    res = vrc.compare(base, cand, mode="strict")
    assert res["verdict"] == "FLAG"
    view = res["views"][0]
    assert view["status"] == "CHANGED"
    assert view["baseline_dimensions"] != view["candidate_dimensions"]


# --------------------------------------------------------------------------------------
# Missing files — reported, never a crash
# --------------------------------------------------------------------------------------
def test_missing_candidate_is_reported_not_crash(tmp_path):
    png = _make_png(8, 8)
    base = _layout(tmp_path / "base", {"newbie/table.png": png, "newbie/map.png": png})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": png})  # map.png absent
    res = vrc.compare(base, cand, mode="strict")
    assert res["counts"]["missing_candidate"] == 1
    missing = [v for v in res["views"] if v["status"] == "MISSING_CANDIDATE"]
    assert len(missing) == 1
    assert missing[0]["view"] == "newbie/map.png"
    # A baseline with no candidate is a regression-worthy FLAG (the view vanished from the run).
    assert res["verdict"] == "FLAG"


def test_missing_baseline_is_reported_not_a_regression(tmp_path):
    png = _make_png(8, 8)
    base = _layout(tmp_path / "base", {"newbie/table.png": png})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": png, "newbie/new_view.png": png})
    res = vrc.compare(base, cand, mode="strict")
    assert res["counts"]["missing_baseline"] == 1
    extra = [v for v in res["views"] if v["status"] == "MISSING_BASELINE"]
    assert len(extra) == 1
    assert extra[0]["view"] == "newbie/new_view.png"
    # A new candidate view with no baseline is NOT a regression — nothing to compare against.
    assert res["verdict"] == "PASS"


def test_nonexistent_dirs_do_not_crash(tmp_path):
    res = vrc.compare(tmp_path / "nope-base", tmp_path / "nope-cand", mode="strict")
    assert res["verdict"] in ("PASS", "EMPTY")
    assert res["counts"]["matched"] == 0


# --------------------------------------------------------------------------------------
# AUDIT mode — perceptual; SKIP cleanly when PIL is absent, run when present
# --------------------------------------------------------------------------------------
def test_audit_mode_behaves_per_pil_availability(tmp_path):
    png = _make_png(8, 8, color=(10, 20, 30))
    base = _layout(tmp_path / "base", {"newbie/table.png": png})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": png})
    res = vrc.compare(base, cand, mode="audit")
    assert res["mode"] == "audit"
    if not vrc.perceptual_available():
        # Clean skip: explicit, never a crash, never a false FLAG.
        assert res["verdict"] == "SKIPPED"
        assert "skip_reason" in res
        assert res["skip_reason"]  # non-empty human-readable message
    else:
        # Identical images -> zero perceptual distance -> PASS.
        assert res["verdict"] == "PASS"
        view = res["views"][0]
        assert view.get("distance") == 0 or view.get("distance") == pytest.approx(0.0)


@pytest.mark.skipif(not _HAVE_PIL, reason="PIL absent; perceptual diff cannot run")
def test_audit_mode_detects_difference_when_pil_present(tmp_path):
    a = _png_bytes_pil(8, 8, (0, 0, 0))
    b = _png_bytes_pil(8, 8, (255, 255, 255))
    base = _layout(tmp_path / "base", {"newbie/table.png": a})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": b})
    res = vrc.compare(base, cand, mode="audit")
    assert res["verdict"] == "PASS"  # AUDIT never gates; it reports for human review
    view = res["views"][0]
    assert view["distance"] > 0
    # An above-threshold change is annotated as DIFF (advisory), not a hard FLAG.
    assert view["status"] in ("DIFF", "PASS")


@pytest.mark.skipif(not _HAVE_PIL, reason="PIL absent; perceptual diff cannot run")
def test_audit_html_report_written_to_tmp(tmp_path):
    a = _png_bytes_pil(8, 8, (0, 0, 0))
    b = _png_bytes_pil(8, 8, (255, 255, 255))
    base = _layout(tmp_path / "base", {"newbie/table.png": a})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": b})
    out_html = tmp_path / "report.html"
    res = vrc.compare(base, cand, mode="audit", report_html=out_html)
    assert out_html.exists()
    text = out_html.read_text()
    assert "newbie/table.png" in text
    assert res["report_html"] == str(out_html)


# --------------------------------------------------------------------------------------
# ADDITIVE-BY-DEFAULT — empty inputs == no change (no FLAG, no crash)
# --------------------------------------------------------------------------------------
def test_empty_baseline_dir_is_additive_noop(tmp_path):
    base = _layout(tmp_path / "base", {})  # exists but empty
    cand = _layout(tmp_path / "cand", {"newbie/table.png": _make_png(8, 8)})
    res = vrc.compare(base, cand, mode="strict")
    # No baselines to compare -> not a regression. Candidate-only views reported, not flagged.
    assert res["verdict"] in ("PASS", "EMPTY")
    assert res["counts"]["changed"] == 0


# --------------------------------------------------------------------------------------
# CLI smoke — exit codes + --json, writing nothing outside tmp
# --------------------------------------------------------------------------------------
def test_cli_strict_pass_exit_zero_and_json(tmp_path, capsys):
    png = _make_png(8, 8)
    base = _layout(tmp_path / "base", {"newbie/table.png": png})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": png})
    rc = vrc.main(
        ["--baseline-dir", str(base), "--candidate-dir", str(cand), "--mode", "strict", "--json"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "PASS"
    assert out["mode"] == "strict"


def test_cli_strict_flag_exit_two(tmp_path):
    base = _layout(tmp_path / "base", {"newbie/table.png": _make_png(8, 8, byte_fill=0)})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": _make_png(8, 8, byte_fill=255, color=(9, 9, 9))})
    rc = vrc.main(
        ["--baseline-dir", str(base), "--candidate-dir", str(cand), "--mode", "strict", "--json"]
    )
    assert rc == 2  # FLAG -> non-zero so CI can gate


def test_cli_audit_never_gates_exit_zero(tmp_path):
    png = _make_png(8, 8)
    base = _layout(tmp_path / "base", {"newbie/table.png": png})
    cand = _layout(tmp_path / "cand", {"newbie/table.png": png})
    rc = vrc.main(
        ["--baseline-dir", str(base), "--candidate-dir", str(cand), "--mode", "audit", "--json"]
    )
    # AUDIT is advisory: PASS or SKIPPED, both exit 0 (never gates the build).
    assert rc == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:xdist"]))
