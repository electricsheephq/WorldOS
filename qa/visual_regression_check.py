#!/usr/bin/env python3
"""Did a GUI screen visually REGRESS vs the committed baseline? — the QA harness's
machine-readable visual-regression signal for the implementing agent.

After a GUI sweep captures one PNG per (persona, view) (e.g. via ``qa/owshot.sh`` /
``qa/screen_coverage.py``), the agent asks: *did any screen change in a way that warrants
a human look — or worse, did a whole element/screen VANISH?* This tool answers it by
diffing a candidate screenshot dir against a committed baseline dir. It is a PURE READER:
it never writes state, never runs a game, never touches a committed data artifact.

TWO MODES (start strict; characterise the audit noise floor before gating on it)
--------------------------------------------------------------------------------
* STRICT  — stdlib only (``hashlib`` + a tiny IHDR parse). Compares the sha256 of the PNG
  bytes and the pixel dimensions. Gates ONLY on a DEFINITE change: a byte/dimension diff
  (something rendered differently), or a baseline view with NO candidate (a whole screen
  vanished from the run). A new candidate-only view is NOT a regression (nothing to compare).
  Verdict FLAG -> exit 2 so CI can gate. This is the only mode that should ever gate a build.

* AUDIT  — best-effort PERCEPTUAL diff for human review. Tries ``imagehash`` (perceptual
  hash, Hamming distance), else falls back to a PIL-only mean per-pixel difference, else
  SKIPS cleanly with a clear message if PIL is absent (we deliberately do NOT add a heavy
  dependency to force it to run). AUDIT NEVER gates the build — it always exits 0 (PASS or
  SKIPPED) and optionally emits an HTML/JSON report. Its purpose is to characterise the
  noise floor (font hinting, antialiasing, cursor blink, clock text) BEFORE anyone decides
  which views are stable enough to gate on in strict mode.

ADDITIVE-BY-DEFAULT: an empty/absent baseline dir == today's behaviour (PASS / no flag).
Every gate here reads engine-rendered pixels on disk, never fiction.

BASELINE LAYOUT (see qa/screenshot_baselines/README.md):
    qa/screenshot_baselines/v<X.Y.Z>/<persona>/<view>.png
A "view" key is the path of a PNG RELATIVE to the baseline (or candidate) root, so
``newbie/table.png`` in baseline is compared against ``newbie/table.png`` in candidate.

USAGE
-----
    python qa/visual_regression_check.py \
        --baseline-dir qa/screenshot_baselines/v1.0.4 \
        --candidate-dir /tmp/sweep-shots \
        --mode strict --json

    # From Python:
    from visual_regression_check import compare
    res = compare(baseline_dir, candidate_dir, mode="strict")  # -> verdict dict

Exit codes (so CI / the agent can gate): 0 = PASS / SKIPPED / EMPTY, 2 = FLAG (strict only).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import struct
import sys
from pathlib import Path
from typing import Optional

_PNG_SIG = b"\x89PNG\r\n\x1a\n"

# Verdict -> process exit code. AUDIT never gates, so it can only ever yield 0-mapped verdicts.
_EXIT = {"PASS": 0, "SKIPPED": 0, "EMPTY": 0, "FLAG": 2}

# AUDIT noise-floor default: an advisory DIFF threshold, NOT a gate. Annotates views whose
# perceptual distance exceeds it as "DIFF" for human attention; below it stays "PASS".
# (See README: characterise the real noise floor on your host before trusting this number.)
DEFAULT_AUDIT_THRESHOLD = 0.0


# ======================================================================================
# stdlib primitives (STRICT mode) — no third-party imports
# ======================================================================================
def sha256_file(path: Path | str) -> str:
    """sha256 hex digest of a file's raw bytes (read in chunks; pure stdlib)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def png_dimensions(data: bytes) -> Optional[tuple[int, int]]:
    """(width, height) parsed from a PNG's IHDR chunk, or None if ``data`` is not a PNG.
    Pure stdlib — does NOT need PIL. The IHDR is the first chunk: width/height are the two
    big-endian uint32s at offsets 16/20 (8-byte signature + 4-byte length + b'IHDR')."""
    if len(data) < 24 or data[:8] != _PNG_SIG or data[12:16] != b"IHDR":
        return None
    try:
        w, h = struct.unpack(">II", data[16:24])
    except struct.error:
        return None
    return (int(w), int(h))


def _collect_pngs(root: Path) -> dict[str, Path]:
    """Map ``{view_key: path}`` for every ``*.png`` under ``root`` (recursive). view_key is the
    POSIX-style path relative to ``root`` so baseline and candidate keys line up regardless of
    OS path separators. A missing/empty dir yields ``{}`` (additive-by-default: no crash)."""
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return {}
    out: dict[str, Path] = {}
    for p in sorted(root.rglob("*.png")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = p
    return out


# ======================================================================================
# perceptual primitives (AUDIT mode) — optional, dependency-light
# ======================================================================================
def perceptual_available() -> bool:
    """True if AUDIT mode can run a perceptual diff at all (PIL present). We try ``imagehash``
    first for a better metric but fall back to a PIL-only diff, so PIL alone is sufficient."""
    try:
        import PIL  # noqa: F401
    except Exception:
        return False
    return True


def _perceptual_backend() -> str:
    """Which AUDIT backend is available: 'imagehash' > 'pil' > 'none'."""
    if not perceptual_available():
        return "none"
    try:
        import imagehash  # noqa: F401

        return "imagehash"
    except Exception:
        return "pil"


def _perceptual_distance(baseline: Path, candidate: Path, backend: str) -> Optional[float]:
    """A best-effort perceptual distance in [0, 1]-ish terms. 0 == perceptually identical.
    Returns None if the images cannot be opened (reported, never crashes the run)."""
    try:
        from PIL import Image

        with Image.open(baseline) as a_im, Image.open(candidate) as b_im:
            a_im.load()
            b_im.load()
            if backend == "imagehash":
                import imagehash

                ha = imagehash.phash(a_im)
                hb = imagehash.phash(b_im)
                # Hamming distance normalised by the hash bit length -> [0, 1].
                bits = len(ha.hash) * len(ha.hash[0])
                return float(ha - hb) / float(bits) if bits else 0.0
            # PIL-only fallback: normalise to a common size + mode, then mean abs per-channel
            # difference / 255 -> [0, 1]. Crude but dependency-free; good enough to characterise
            # a noise floor for human review (NEVER gates).
            a_rgb = a_im.convert("RGB").resize((32, 32))
            b_rgb = b_im.convert("RGB").resize((32, 32))
            # .tobytes() yields flat R,G,B,R,G,B... and is stable across Pillow versions
            # (avoids the deprecated .getdata()). Mean abs per-channel diff / 255 -> [0, 1].
            a_px = a_rgb.tobytes()
            b_px = b_rgb.tobytes()
            n = min(len(a_px), len(b_px))
            if n == 0:
                return 0.0
            total = sum(abs(a_px[i] - b_px[i]) for i in range(n))
            return (total / n) / 255.0
    except Exception:
        return None


# ======================================================================================
# comparison core
# ======================================================================================
def compare(
    baseline_dir: Path | str,
    candidate_dir: Path | str,
    *,
    mode: str = "strict",
    audit_threshold: float = DEFAULT_AUDIT_THRESHOLD,
    report_html: Optional[Path | str] = None,
) -> dict:
    """Compare candidate screenshots against baselines. Returns a machine-readable verdict dict.

    mode='strict' : stdlib sha256+dims; FLAG on any byte/dim change or a vanished baseline view.
    mode='audit'  : perceptual diff for human review; never FLAGs (PASS/SKIPPED only)."""
    mode = (mode or "strict").lower()
    if mode not in ("strict", "audit"):
        raise ValueError(f"unknown mode {mode!r} (expected 'strict' or 'audit')")

    base_root = Path(baseline_dir)
    cand_root = Path(candidate_dir)
    baselines = _collect_pngs(base_root)
    candidates = _collect_pngs(cand_root)

    result: dict = {
        "mode": mode,
        "baseline_dir": str(base_root),
        "candidate_dir": str(cand_root),
        "views": [],
        "counts": {
            "matched": 0,
            "changed": 0,
            "diff": 0,
            "missing_candidate": 0,
            "missing_baseline": 0,
            "errored": 0,
        },
    }

    all_keys = sorted(set(baselines) | set(candidates))

    if mode == "audit":
        return _compare_audit(result, all_keys, baselines, candidates, audit_threshold, report_html)
    return _compare_strict(result, all_keys, baselines, candidates)


def _compare_strict(result: dict, keys, baselines, candidates) -> dict:
    counts = result["counts"]
    for view in keys:
        b = baselines.get(view)
        c = candidates.get(view)
        if b is not None and c is None:
            # A baseline view with no candidate = the screen vanished from the run -> regression.
            counts["missing_candidate"] += 1
            result["views"].append({"view": view, "status": "MISSING_CANDIDATE",
                                     "baseline_sha256": _safe_hash(b), "candidate_sha256": None})
            continue
        if b is None and c is not None:
            # A candidate view with no baseline = a NEW screen. Not a regression (nothing to
            # compare); reported so a human can promote it into the next baseline set.
            counts["missing_baseline"] += 1
            result["views"].append({"view": view, "status": "MISSING_BASELINE",
                                    "baseline_sha256": None, "candidate_sha256": _safe_hash(c)})
            continue
        # Both present: compare content hash + dimensions.
        b_hash, b_dims, b_err = _hash_and_dims(b)
        c_hash, c_dims, c_err = _hash_and_dims(c)
        entry = {
            "view": view,
            "baseline_sha256": b_hash,
            "candidate_sha256": c_hash,
            "baseline_dimensions": list(b_dims) if b_dims else None,
            "candidate_dimensions": list(c_dims) if c_dims else None,
        }
        if b_err or c_err:
            counts["errored"] += 1
            entry["status"] = "ERROR"
            entry["error"] = b_err or c_err
            result["views"].append(entry)
            continue
        if b_hash == c_hash and b_dims == c_dims:
            counts["matched"] += 1
            entry["status"] = "PASS"
        else:
            counts["changed"] += 1
            entry["status"] = "CHANGED"
        result["views"].append(entry)

    # FLAG on a definite change OR a vanished baseline view. A new candidate-only view does
    # NOT flag. No comparable baselines at all -> EMPTY (additive-by-default no-op).
    flagged = counts["changed"] > 0 or counts["missing_candidate"] > 0 or counts["errored"] > 0
    if flagged:
        result["verdict"] = "FLAG"
    elif not baselines:
        result["verdict"] = "EMPTY"
    else:
        result["verdict"] = "PASS"
    result["message"] = _summary(result)
    return result


def _compare_audit(result, keys, baselines, candidates, threshold, report_html) -> dict:
    backend = _perceptual_backend()
    result["backend"] = backend
    result["audit_threshold"] = threshold

    if backend == "none":
        # Clean skip — explicit, never a crash, never a false FLAG.
        result["verdict"] = "SKIPPED"
        result["skip_reason"] = (
            "AUDIT (perceptual) mode needs Pillow (PIL); it is not importable in this "
            "environment. Install it (e.g. `uv pip install pillow`, optionally `imagehash` "
            "for a perceptual-hash metric) or use --mode strict, which is stdlib-only. "
            "AUDIT is advisory and never gates the build."
        )
        result["message"] = result["skip_reason"]
        return result

    counts = result["counts"]
    for view in keys:
        b = baselines.get(view)
        c = candidates.get(view)
        if b is not None and c is None:
            counts["missing_candidate"] += 1
            result["views"].append({"view": view, "status": "MISSING_CANDIDATE", "distance": None})
            continue
        if b is None and c is not None:
            counts["missing_baseline"] += 1
            result["views"].append({"view": view, "status": "MISSING_BASELINE", "distance": None})
            continue
        dist = _perceptual_distance(b, c, backend)
        if dist is None:
            counts["errored"] += 1
            result["views"].append({"view": view, "status": "ERROR", "distance": None,
                                    "error": "could not open one of the images"})
            continue
        if dist > threshold:
            counts["diff"] += 1
            status = "DIFF"
        else:
            counts["matched"] += 1
            status = "PASS"
        result["views"].append({"view": view, "status": status, "distance": round(dist, 6),
                                 "baseline": str(b), "candidate": str(c)})

    # AUDIT NEVER gates: PASS regardless of how many DIFFs — it is for human review only.
    result["verdict"] = "PASS"
    if report_html is not None:
        out = Path(report_html)
        out.write_text(_render_html(result))
        result["report_html"] = str(out)
    result["message"] = _summary(result)
    return result


# ======================================================================================
# helpers
# ======================================================================================
def _safe_hash(path: Path) -> Optional[str]:
    try:
        return sha256_file(path)
    except OSError:
        return None


def _hash_and_dims(path: Path):
    """Return (sha256|None, (w,h)|None, error|None). Never raises."""
    try:
        data = path.read_bytes()
    except OSError as e:
        return (None, None, f"read failed: {e}")
    return (hashlib.sha256(data).hexdigest(), png_dimensions(data), None)


def _summary(result: dict) -> str:
    c = result["counts"]
    lines = [f"{result['verdict']} ({result['mode']}): "
             f"matched={c['matched']} changed={c['changed']} diff={c['diff']} "
             f"missing_candidate={c['missing_candidate']} missing_baseline={c['missing_baseline']} "
             f"errored={c['errored']}"]
    if result.get("skip_reason"):
        return result["skip_reason"]
    for v in result["views"]:
        if v["status"] in ("PASS",):
            continue
        extra = ""
        if "distance" in v and v["distance"] is not None:
            extra = f" distance={v['distance']}"
        elif v["status"] == "CHANGED":
            extra = f" {v.get('baseline_dimensions')} -> {v.get('candidate_dimensions')}"
        lines.append(f"  {v['status']:18s} {v['view']}{extra}")
    return "\n".join(lines)


def _render_html(result: dict) -> str:
    """A minimal self-contained HTML report for human review of AUDIT diffs. Embeds nothing
    binary (just links the on-disk paths) so it stays tiny and writes wherever the caller asks."""
    rows = []
    for v in result["views"]:
        view = html.escape(v["view"])
        status = html.escape(v["status"])
        dist = v.get("distance")
        dist_s = "" if dist is None else f"{dist}"
        base = html.escape(v.get("baseline", "") or "")
        cand = html.escape(v.get("candidate", "") or "")
        rows.append(
            f"<tr><td>{view}</td><td>{status}</td><td>{dist_s}</td>"
            f"<td>{base}</td><td>{cand}</td></tr>"
        )
    c = result["counts"]
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<title>WorldOS visual-regression AUDIT report</title>"
        "<style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px}</style>"
        "<h1>Visual-regression AUDIT report</h1>"
        f"<p>mode=audit backend={html.escape(str(result.get('backend')))} "
        f"threshold={result.get('audit_threshold')}</p>"
        f"<p>matched={c['matched']} diff={c['diff']} "
        f"missing_candidate={c['missing_candidate']} missing_baseline={c['missing_baseline']} "
        f"errored={c['errored']}</p>"
        "<p><em>AUDIT is advisory and never gates the build. Characterise the noise floor "
        "before promoting any view into a strict gate.</em></p>"
        "<table><tr><th>view</th><th>status</th><th>distance</th>"
        "<th>baseline</th><th>candidate</th></tr>"
        + "".join(rows)
        + "</table>"
    )


# ======================================================================================
# CLI
# ======================================================================================
def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--baseline-dir", required=True, help="committed baseline screenshot dir")
    p.add_argument("--candidate-dir", required=True, help="candidate (this-run) screenshot dir")
    p.add_argument("--mode", choices=["strict", "audit"], default="strict",
                   help="strict = stdlib hash/dim gate (default); audit = perceptual review")
    p.add_argument("--audit-threshold", type=float, default=DEFAULT_AUDIT_THRESHOLD,
                   help="AUDIT-only advisory DIFF threshold in [0,1] (never gates)")
    p.add_argument("--report-html", help="AUDIT-only: write an HTML report to this path")
    p.add_argument("--json", action="store_true", help="emit the machine-readable verdict as JSON")
    args = p.parse_args(argv)

    result = compare(
        args.baseline_dir,
        args.candidate_dir,
        mode=args.mode,
        audit_threshold=args.audit_threshold,
        report_html=args.report_html,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result.get("message", result["verdict"]))
    return _EXIT.get(result["verdict"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
