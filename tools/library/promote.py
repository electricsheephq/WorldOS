#!/usr/bin/env python3
"""promote.py — the HV3 eval-gated promotion pipeline (Act II §4c, #1325).

WHAT IT IS
----------
The harvest loop's PROMOTION gate. It reads a nomination queue (qa/nominations.jsonl), looks up each
nominated artifact's panel score (from the additive `artifacts` table HV1 populated, or — cleanly
separated, offline-skippable — scores it if unscored), applies the threshold gate, and writes the ones
that pass into the pack-shaped repo-root ``library/`` as reusable, tiered entries.

    nominations (qa/nominations.jsonl)
      → [score-if-unscored]  (a cleanly separated step; NO test depends on it running live)
      → threshold gate       (overall >= 4.0 AND every dim >= 3.0 AND control-valid → tier=stable)
      → library/ entries + artifacts-table rows

promote.py is the SOLE WRITER of ``library/``. It is ADDITIVE by default (an empty nomination queue
leaves the library byte-identical). It NEVER edits room_recipes.json or the asset registry — room
entries REFERENCE recipe keys + asset_ids by value only (byte-identity of both files is a hard
invariant, asserted in tests).

THE GATE — TWO STRATEGIES BY CLASS (epic #1325; visual split ratified 2026-07-08,
docs/roadmap/VISUAL-PROMOTION-GATE-DECISION.md)
------------------------------------------------------------------------------------------------------
``GATE_STRATEGIES[class]`` selects the gate. The classes split into TEXT and VISUAL because their
scoring doctrines genuinely differ (measured): text artifacts have citable 1-5 absolute thresholds;
image panels do NOT (blind, real shipped PoE2/BG2 art scores 3.0-5.6 — an absolute number is never a
quality verdict), so the visual gate is delta-anchored, never absolute.

  * TEXT classes (quest / npc / location / encounter) → the byte-unchanged threshold gate
    (``evaluate_gate``). ``tier=stable`` iff ALL of:
      - overall >= PROMOTE_OVERALL_MIN (4.0),
      - every per-dimension score >= PROMOTE_DIM_MIN (3.0), and
      - control-valid — the panel that produced the score had its disguised canon controls land in-band
        (qa/artifact_calibration_panel's ``panel_valid``; derived here from the panel's control rows).
  * VISUAL classes ("room" today) → the delta-anchored visual gate (``evaluate_visual_gate``), which
    reads the panel JSON at the nomination's ``source_path`` (NOT the `artifacts` DB table — visual
    scores land in runs/surface=visual + panel JSONs). ``tier=stable`` iff ALL of, with NO absolute
    threshold:
      - the deterministic pre-gate HARD FLOOR PASSed (frame-lit + occupancy + pin/floor-contact; the
        stylistic G6 staging-law band and reel-only G5 motion are NOT promotion floors — daylight
        plates legitimately sit outside the dark-chiaroscuro band),
      - the panel cited a control REGISTERED in qa/visual_controls_identity.json whose same-panel
        median landed inside its band (the instrument was valid this panel), and
      - candidate-minus-control delta >= -noise_law (-1.2 on the 0-10 panel scale).

``tier=canonical`` is HUMAN curation ONLY — promote.py never assigns it (either strategy). A nomination
that fails its gate is recorded as processed (so --batch is idempotent) but NOT written to the library.

THE NOMINATION QUEUE (bootstrap ruling, epic addendum [HIGH])
-------------------------------------------------------------
Nothing upstream of HV3 produces qa/nominations.jsonl yet — HV5 (#1327, qa/closeout.py auto-nominator)
does, and it depends ON HV3. So for the FIRST batch this file is bootstrapped BY HAND, one JSON line
per artifact_id, sourced from HV2's qa/artifacts_out/<campaign>/**/*.json listings. promote.py does NOT
invent its own nomination heuristic — that logic belongs solely to HV5's closeout auto-nominator. Each
line is a JSON object; the ONLY required key is ``artifact_id``. Optional keys:
  * ``class``       — the entry class. Selects the gate STRATEGY via GATE_STRATEGIES: absent / a text
    class (quest/npc/location/encounter) → the text gate (class then read from the scored DB row, as
    before); ``"room"`` → the VISUAL gate. A VISUAL nomination MUST declare its class here (its score
    lives in a panel JSON, not the `artifacts` table, so the class can't be read from a DB row).
  * ``source_path`` — repo-relative/absolute path. TEXT: the extracted artifact JSON (for score-if-
    unscored + entry provenance). VISUAL: the control-anchored panel JSON the visual gate reads.
  * ``room_ref``    — ``{recipe_key, asset_ids}`` a room entry REFERENCES by value (never inlined).
  * ``license``     — SPDX-ish license string for the entry (else --license default; else pack.json's).
  * ``curation_note`` — free text carried onto the entry.

PANEL INVOCATION (epic addendum [MED])
--------------------------------------
The score-if-unscored step calls HV1's plain function ``artifact_score.score_artifact_panel`` (added by
HV3 to artifact_score.py as the callable entrypoint the addendum requires). promote.py NEVER spawns
agent sub-tasks itself; any agent fan-out lives upstream in artifact_score.py. When the scorer is
offline (the current claude -p auth outage), run with ``--dry-run`` (no scoring, no library writes —
just the gate verdict on whatever is already scored) or ``--skip-unscored`` (promote only the
already-scored nominations; leave the unscored ones for a later live batch).

CLI CONTRACT (epic addendum [MED], owned here as HV3 implements it first)
------------------------------------------------------------------------
    promote.py --batch [--library DIR] [--nominations FILE] [--db qa/scores.db]
               [--license SPDX] [--skip-unscored] [--dry-run] [--budget 1.50]

``--batch`` reads qa/nominations.jsonl top-to-bottom, processes every UNPROCESSED line (idempotent —
re-running skips already-promoted / already-processed artifact_ids via the processed-log), exits 0 even
with zero promotions. There is no single-nomination mode in v1. Each nomination is routed to its gate
STRATEGY by class (GATE_STRATEGIES): text classes take the DB-backed threshold gate; a ``room``
nomination takes the visual gate (reads its panel JSON at ``source_path``; --skip-unscored / --dry-run
still apply — --dry-run previews the visual verdict and writes nothing; score-if-unscored is text-only).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent.parent
_QA_DIR = _REPO_ROOT / "qa"
sys.path.insert(0, str(_QA_DIR))

import scores_db  # noqa: E402

# ── Gate constants (the ratified thresholds) ────────────────────────────────────────────────────
PROMOTE_OVERALL_MIN = 4.0
PROMOTE_DIM_MIN = 3.0

# ── Library layout ──────────────────────────────────────────────────────────────────────────────
DEFAULT_LIBRARY_DIR = _REPO_ROOT / "library"
DEFAULT_NOMINATIONS = _QA_DIR / "nominations.jsonl"
ENTRY_CLASSES: tuple[str, ...] = ("quest", "npc", "location", "encounter", "room")
_CLASS_TO_SUBDIR = {c: c + "s" for c in ENTRY_CLASSES}  # quest→quests, room→rooms, …
PACK_JSON = "pack.json"
PROCESSED_LOG = ".promoted.jsonl"  # append-only marker log: one line per processed nomination
DEFAULT_LICENSE = "proprietary"
PACK_VERSION = "0.1.0"


# ── Nomination queue ────────────────────────────────────────────────────────────────────────────
def read_nominations(path: Path | str) -> list[dict]:
    """Read qa/nominations.jsonl → a list of nomination dicts (one per non-blank line).

    Each line must be a JSON object with at least ``artifact_id``. A missing file yields [] (a batch
    with nothing to do exits 0). Blank lines are skipped; a malformed line raises loudly with its
    line number (a hand-authored bootstrap file should fail fast on a typo)."""
    p = Path(path)
    if not p.exists():
        return []
    noms: list[dict] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p}:{i}: not valid JSON: {e}") from e
        if not isinstance(obj, dict) or "artifact_id" not in obj:
            raise ValueError(f"{p}:{i}: nomination must be a JSON object with an 'artifact_id'")
        noms.append(obj)
    return noms


# ── Processed-log (idempotency) ─────────────────────────────────────────────────────────────────
def _processed_log_path(library_dir: Path) -> Path:
    return library_dir / PROCESSED_LOG


def read_processed(library_dir: Path) -> set[str]:
    """Set of artifact_ids already processed by a prior batch (promoted OR gate-rejected)."""
    p = _processed_log_path(library_dir)
    if not p.exists():
        return set()
    done: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            done.add(json.loads(line)["artifact_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return done


def _append_processed(library_dir: Path, artifact_id: str, verdict: str, tier: Optional[str]) -> None:
    """Append one marker line so a re-run skips this artifact_id (idempotency)."""
    p = _processed_log_path(library_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"artifact_id": artifact_id, "verdict": verdict, "tier": tier,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


# ── Score lookup + control-validity ─────────────────────────────────────────────────────────────
def _artifacts_by_id(db_path: Path | str) -> dict[str, dict]:
    """Index the `artifacts` table by artifact_id (newest row wins — fetch_artifacts is ts-desc)."""
    by_id: dict[str, dict] = {}
    for row in scores_db.fetch_artifacts(db_path):
        by_id.setdefault(row["artifact_id"], row)
    return by_id


def control_valid_for_panel(panel_id: Optional[str], rows: list[dict]) -> bool:
    """Was the panel that scored this artifact control-valid?

    A panel is control-valid iff it contained at least one disguised canon control AND every control's
    median landed inside its expected band. We reconstruct the verdict from the `artifacts` rows sharing
    ``panel_id``: each control row (is_control=1) carries its ``overall`` and its ``control_anchor``; the
    band is [anchor - NOISE, anchor + NOISE] with NOISE from the identity map. A panel with NO controls
    is NOT control-valid (we cannot vouch for the instrument on that run) — fail closed for `stable`.

    ``rows`` is the full artifacts table (list of dicts) so this is a pure function over already-fetched
    data (no second DB round-trip). A ``panel_id`` of None (a lone --no-panel single score) is never
    control-valid.
    """
    if not panel_id:
        return False
    noise = _control_noise_law()
    controls = [r for r in rows if r.get("panel_id") == panel_id and r.get("is_control")]
    if not controls:
        return False
    for r in controls:
        anchor = r.get("control_anchor")
        overall = r.get("overall")
        if anchor is None or overall is None:
            return False
        if not (anchor - noise <= overall <= anchor + noise):
            return False
    return True


def _control_noise_law() -> float:
    """The ±band half-width for a control (qa/artifact_controls_identity.json noise_law; default 1.2)."""
    ident = _QA_DIR / "artifact_controls_identity.json"
    try:
        return float(json.loads(ident.read_text(encoding="utf-8")).get("noise_law", 1.2))
    except (OSError, ValueError, json.JSONDecodeError):
        return 1.2


# ── The gate ────────────────────────────────────────────────────────────────────────────────────
class GateResult:
    __slots__ = ("passed", "tier", "reasons", "overall", "dims", "control_valid")

    def __init__(self, passed: bool, tier: Optional[str], reasons: list[str],
                 overall: Optional[float], dims: dict, control_valid: bool):
        self.passed = passed
        self.tier = tier
        self.reasons = reasons
        self.overall = overall
        self.dims = dims
        self.control_valid = control_valid

    def as_dict(self) -> dict:
        return {"passed": self.passed, "tier": self.tier, "reasons": self.reasons,
                "overall": self.overall, "dims": self.dims, "control_valid": self.control_valid}


def evaluate_gate(score_row: dict, *, control_valid: bool) -> GateResult:
    """Apply the ratified threshold gate to one scored `artifacts` row.

    stable iff overall >= 4.0 AND every dim >= 3.0 AND control_valid. canonical is NEVER assigned here
    (human curation only). Returns a GateResult with the pass/fail reasons for the batch log."""
    reasons: list[str] = []
    overall = score_row.get("overall")
    dims_raw = score_row.get("dims_json")
    dims: dict = {}
    if isinstance(dims_raw, str):
        try:
            dims = json.loads(dims_raw)
        except json.JSONDecodeError:
            dims = {}
    elif isinstance(dims_raw, dict):
        dims = dims_raw

    if overall is None:
        reasons.append("unscored (no overall)")
    elif overall < PROMOTE_OVERALL_MIN:
        reasons.append(f"overall {overall} < {PROMOTE_OVERALL_MIN}")

    low_dims = {k: v for k, v in dims.items() if isinstance(v, (int, float)) and v < PROMOTE_DIM_MIN}
    if low_dims:
        reasons.append(f"dim(s) below {PROMOTE_DIM_MIN}: {low_dims}")
    if not dims:
        reasons.append("no per-dimension scores")

    if not control_valid:
        reasons.append("panel not control-valid")

    if score_row.get("is_control"):
        reasons.append("row is a disguised canon control, not a promotable candidate")

    passed = not reasons
    return GateResult(passed, "stable" if passed else None, reasons, overall, dims, control_valid)


# ── The VISUAL gate (class="room" and future visual classes) ─────────────────────────────────────
# The image analogue of evaluate_gate. Its PASS rule encodes the visual-critic doctrine (decision
# docs/roadmap/VISUAL-PROMOTION-GATE-DECISION.md): NO absolute threshold (an absolute panel number is
# never a quality verdict for images), a delta anchored to a REGISTERED disguised-real-art control, and
# a deterministic pre-gate hard floor. It reads a PANEL JSON (the nomination's source_path), NOT the
# `artifacts` DB table — visual scores land in runs/surface=visual + panel JSONs.
VISUAL_CLASSES: tuple[str, ...] = ("room",)
GATE_STRATEGIES: dict[str, str] = {  # class -> strategy; absent/text class -> "text" (byte-unchanged)
    "quest": "text", "npc": "text", "location": "text", "encounter": "text", "room": "visual",
}
_VISUAL_CONTROLS_IDENTITY = _QA_DIR / "visual_controls_identity.json"
# ── The two-tier delta ladder (architect amendment 2026-07-08) ───────────────────────────────────
# A single delta>=-1.2 PASS bar means "at statistical PARITY with real shipped PoE2 art" — the
# DESTINATION bar, not the era-appropriate ADOPTION bar. Two measured taste-gated panels calibrate the
# gap: camp_clearing_night (delta -2.0) PASSED both human taste-gates, so the adoption bar must sit
# BELOW -2.0; market_square (delta -5.0) was a clear taste-REJECT. So the verdict ladder is:
#   delta >= VIS_DELTA_PARITY (-1.2)                     → tier "canonical-candidate" (parity with real
#                                                          art; a human MAY promote to canonical) — PASS.
#   VIS_DELTA_ADOPT (-2.5) <= delta < VIS_DELTA_PARITY   → tier "stable" (adopted-quality) — PASS.
#   delta < VIS_DELTA_ADOPT (-2.5)                       → REJECT.
# VIS_DELTA_PARITY is the noise law (parity == within-noise of real art; == the registry noise_law).
# VIS_DELTA_ADOPT=-2.5 is the era-appropriate adoption bar, calibrated 2026-07-08 on the two anchors:
# camp_clearing_night delta -2.0 (taste-PASS) / market_square delta -5.0 (taste-REJECT). A later
# taste/noise re-measure that moves the bar changes ONE constant here.
VIS_DELTA_PARITY = -1.2
VIS_DELTA_ADOPT = -2.5
# The pre-gate gates that are a PROMOTION hard floor. The stylistic G6 luma-staging-law band (a
# daylight plaza legitimately sits outside the dark-chiaroscuro band) and the reel-only G5 motion
# liveness are deliberately EXCLUDED — they filter scorer spend / reels, they do not block adoption.
_VISUAL_HARD_FLOOR_GATES = frozenset(
    {"G1_frame_lit", "G2_occupancy", "G3_floor_contact", "G4_screen_scale"})


def _strategy_for(nomination: dict) -> str:
    """Route a nomination to its gate strategy by class (GATE_STRATEGIES). A nomination with no
    ``class`` (every text nomination today) → "text" — the text path then reads the class off the
    scored DB row exactly as before (byte-identical)."""
    return GATE_STRATEGIES.get(nomination.get("class"), "text")


def load_visual_registry(path: Path | str = _VISUAL_CONTROLS_IDENTITY) -> dict:
    """The visual control registry (qa/visual_controls_identity.json). Missing/unreadable → an empty
    registry (no controls) so the gate fails closed rather than raising — a panel can then never be
    control-registered, which is the correct fail-closed verdict."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"controls": {}, "noise_law": 1.2}
    if not isinstance(data, dict):
        return {"controls": {}, "noise_law": 1.2}
    data.setdefault("controls", {})
    data.setdefault("noise_law", 1.2)
    return data


def load_visual_panel(source_path: Path | str) -> dict:
    """Read the control-anchored panel JSON a visual nomination points at (source_path). Relative
    paths resolve against the repo root (matching how a library entry stores 'qa/evidence/...')."""
    p = Path(source_path)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    return json.loads(p.read_text(encoding="utf-8"))


def visual_pregate_floor_passed(pregate: Optional[dict]) -> bool:
    """True iff the deterministic pre-gate HARD FLOOR passed: no hard-floor gate
    (_VISUAL_HARD_FLOOR_GATES) fired CRITICAL/HIGH. ``pregate`` is the recorded run_pregates() output
    (its ``gates`` list). A missing/empty pregate record fails closed (we cannot vouch for the floor).
    The stylistic G6 staging-law band + reel-only G5 are ignored here on purpose (see the constant)."""
    if not isinstance(pregate, dict):
        return False
    gates = pregate.get("gates")
    if not isinstance(gates, list):
        return False
    saw_floor_gate = False
    for g in gates:
        if not isinstance(g, dict) or g.get("gate") not in _VISUAL_HARD_FLOOR_GATES:
            continue
        saw_floor_gate = True
        if g.get("severity") in ("CRITICAL", "HIGH"):
            return False
    # At least G1 (frame-lit) always runs on any decodable plate; if not a single hard-floor gate is
    # present, the pregate did not actually run the floor → fail closed.
    return saw_floor_gate


def _panel_delta(panel: dict) -> Optional[float]:
    """Candidate-minus-control delta from the panel: the explicit numeric field if present, else
    derived from candidate_median - control_median. None if neither is available/numeric."""
    d = panel.get("delta_candidate_minus_control")
    if isinstance(d, (int, float)) and not isinstance(d, bool):
        return float(d)
    cm, com = panel.get("candidate_median"), panel.get("control_median")
    if isinstance(cm, (int, float)) and isinstance(com, (int, float)):
        return float(cm) - float(com)
    return None


def evaluate_visual_gate(panel: dict, *, registry: dict, noise_law: float = 1.2) -> GateResult:
    """Apply the delta-anchored visual gate to one control-anchored panel JSON. PASSES iff ALL of:
      (i)   the deterministic pre-gate HARD FLOOR PASSed,
      (ii)  the panel cited a control REGISTERED in the visual registry whose same-panel median landed
            inside its band (the instrument was valid this panel), and
      (iii) candidate-minus-control delta >= VIS_DELTA_ADOPT (the two-tier ladder).
    On a PASS the tier is "canonical-candidate" (delta >= VIS_DELTA_PARITY, parity with real art) or
    "stable" (adopted-quality, VIS_DELTA_ADOPT <= delta < VIS_DELTA_PARITY). NO absolute-threshold check
    — the candidate_median is carried for provenance only, never gated. ``noise_law`` is kept for
    call-site compatibility; the ladder uses the module constants (VIS_DELTA_PARITY == -noise_law by
    construction). Returns a GateResult with the fail reasons."""
    reasons: list[str] = []

    # (i) deterministic pre-gate hard floor
    pregate = panel.get("pregate")
    if not visual_pregate_floor_passed(pregate):
        if not isinstance(pregate, dict) or not isinstance(pregate.get("gates"), list):
            reasons.append("no deterministic pre-gate record (cannot vouch for the hard floor)")
        else:
            blocked = [g.get("gate") for g in pregate["gates"]
                       if isinstance(g, dict) and g.get("gate") in _VISUAL_HARD_FLOOR_GATES
                       and g.get("severity") in ("CRITICAL", "HIGH")]
            reasons.append(f"pre-gate hard floor did not PASS (blocking: {blocked or 'floor never ran'})")

    # (ii) registered, in-band control
    controls = registry.get("controls", {}) if isinstance(registry, dict) else {}
    control_id = panel.get("control_id")
    ctrl = controls.get(control_id) if control_id else None
    control_median = panel.get("control_median")
    if not control_id:
        reasons.append("panel is not control-anchored (no control_id)")
    elif ctrl is None:
        reasons.append(f"control {control_id!r} is not in the visual control registry "
                       f"(qa/visual_controls_identity.json)")
    else:
        band = ctrl.get("band")
        if not (isinstance(control_median, (int, float)) and isinstance(band, (list, tuple))
                and len(band) == 2 and band[0] <= control_median <= band[1]):
            reasons.append(f"registered control median {control_median} outside its band {band} — "
                           f"the instrument was not valid this panel")

    # (iii) candidate-vs-control delta on the two-tier ladder (NO absolute threshold). Epsilon at each
    # boundary so a delta EXACTLY at the bar counts on the passing side (float subtraction of medians).
    delta = _panel_delta(panel)
    visual_tier: Optional[str] = None
    if delta is None:
        reasons.append("no numeric candidate-vs-control delta")
    elif delta < VIS_DELTA_ADOPT - 1e-9:  # below the era-appropriate adoption bar → reject
        reasons.append(f"delta {delta} < {VIS_DELTA_ADOPT} adoption bar "
                       f"(candidate below the adopted-quality band)")
    elif delta >= VIS_DELTA_PARITY - 1e-9:  # at/above parity with real art
        visual_tier = "canonical-candidate"
    else:  # VIS_DELTA_ADOPT <= delta < VIS_DELTA_PARITY → adopted-quality
        visual_tier = "stable"

    passed = not reasons
    control_valid = bool(ctrl is not None and not any("instrument was not valid" in r for r in reasons))
    dims = {
        "scores": panel.get("scores"),
        "candidate_median": panel.get("candidate_median"),
        "control_median": control_median,
        "delta_candidate_minus_control": delta,
    }
    return GateResult(passed, visual_tier if passed else None, reasons,
                      panel.get("candidate_median"), dims, control_valid)


# ── Library entry writing ───────────────────────────────────────────────────────────────────────
def ensure_pack(library_dir: Path, *, name: str = "worldos-harvest", license: str = DEFAULT_LICENSE,
                provenance: Optional[dict] = None) -> Path:
    """Ensure library/ + pack.json + the per-class subdirs exist. Idempotent; ADDITIVE — an existing
    pack.json is left untouched (promote.py never rewrites pack metadata after creation)."""
    library_dir.mkdir(parents=True, exist_ok=True)
    for sub in _CLASS_TO_SUBDIR.values():
        (library_dir / sub).mkdir(parents=True, exist_ok=True)
    pack_path = library_dir / PACK_JSON
    if not pack_path.exists():
        pack = {"name": name, "version": PACK_VERSION, "license": license,
                "provenance": provenance or {"produced_by": "tools/library/promote.py",
                                             "roadmap": "Act II §4c HV3 (#1325)"}}
        pack_path.write_text(json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return pack_path


def _entry_filename(artifact_id: str) -> str:
    """Filesystem-safe filename 1:1 with the artifact_id (mirrors HV2's slug+hash discipline)."""
    import hashlib
    import re
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", artifact_id)
    digest = hashlib.sha1(artifact_id.encode("utf-8")).hexdigest()[:8]
    return f"{slug}__{digest}.json"


def build_entry(artifact_id: str, cls: str, score_row: dict, gate: GateResult, *,
                license: str, promoted_at: str, curation_note: Optional[str] = None,
                payload: Optional[dict] = None,
                room_ref: Optional[dict] = None) -> dict:
    """Build a library entry's metadata dict (the ratified entry shape).

    {artifact_id, class, provenance, scores{dims, overall, panel_id, ac_ruler}, tier, reuse_count,
     license, promoted_at, curation_note}. ROOM entries additionally carry a ``room_ref`` REFERENCING
     room_recipes.json recipe keys + registry asset_ids (never a copy of the recipe/registry data)."""
    provenance = {
        "run_id": score_row.get("run_id"),
        "world": score_row.get("world"),
        "sha": score_row.get("sha"),
        "source_path": score_row.get("source_path"),
    }
    entry = {
        "artifact_id": artifact_id,
        "class": cls,
        "provenance": provenance,
        "scores": {
            "dims": gate.dims,
            "overall": gate.overall,
            "panel_id": score_row.get("panel_id"),
            "ac_ruler": score_row.get("ac_ruler"),
        },
        "tier": gate.tier,
        "reuse_count": 0,
        "license": license,
        "promoted_at": promoted_at,
        "curation_note": curation_note,
    }
    if payload is not None:
        entry["payload"] = payload
    if cls == "room" and room_ref is not None:
        # A room entry REFERENCES recipe keys + asset_ids by value; promote.py never edits either source.
        entry["room_ref"] = {"recipe_key": room_ref.get("recipe_key"),
                             "asset_ids": list(room_ref.get("asset_ids", []))}
    return entry


def write_entry(library_dir: Path, entry: dict) -> Path:
    """Write ONE library entry JSON under library/<class>s/. Deterministic bytes (sorted keys)."""
    cls = entry["class"]
    sub = _CLASS_TO_SUBDIR.get(cls)
    if sub is None:
        raise ValueError(f"unknown entry class {cls!r}; expected one of {ENTRY_CLASSES}")
    cls_dir = library_dir / sub
    cls_dir.mkdir(parents=True, exist_ok=True)
    path = cls_dir / _entry_filename(entry["artifact_id"])
    path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# ── Score-if-unscored (cleanly separated; no test depends on it running live) ────────────────────
def score_if_unscored(nomination: dict, *, budget: str = "1.50",
                      db_path: Path | str = scores_db.DB_PATH) -> Optional[dict]:
    """Run HV1's panel on an unscored nomination, returning the fresh `artifacts` row (or None).

    Cleanly separated so the promotion PATH is fully testable without a live scorer: --dry-run and
    --skip-unscored both bypass this. It imports artifact_score lazily and calls its plain
    ``score_artifact_panel`` entrypoint (no agent fan-out here — that lives upstream in artifact_score).
    Requires the nomination to carry ``source_path`` (the artifact JSON to score). Fails fast if the
    loaded artifact's ``artifact_id`` does not match the nomination's (source_path drift/typo) rather
    than silently scoring the wrong artifact. Forwards ``panel_id``/``scorer_model`` from the
    nomination when present so a live-scored row can still land in a control-valid panel and pass the
    gate on a later batch (an un-panel'd row is otherwise permanently unpromotable — panel_id=None is
    never control-valid, see control_valid_for_panel)."""
    aid = nomination["artifact_id"]
    src = nomination.get("source_path")
    if not src:
        raise ValueError(f"cannot score unscored nomination {aid!r}: no 'source_path'")
    import artifact_score  # lazy: keeps the offline promotion path import-clean
    artifact = artifact_score.load_artifact(Path(src))
    if artifact.get("artifact_id") != aid:
        raise ValueError(
            f"source_path {src!r} loaded artifact_id {artifact.get('artifact_id')!r}, "
            f"expected {aid!r} from the nomination — refusing to score a mismatched artifact")
    artifact_score.score_artifact_panel(
        artifact, budget=budget, db_path=db_path,
        panel_id=nomination.get("panel_id"), scorer_model=nomination.get("scorer_model"),
    )
    return _artifacts_by_id(db_path).get(aid)


# ── Paint-drift gate (W6.3, #1462) — a HARD FLOOR on room promotions ─────────────────────────────
def _paint_drift_gate(nom: dict) -> dict:
    """Deterministic paint-drift check for a ROOM nomination (qa/check_plate_drift.py). Runs iff the
    nomination declares a ``candidate_plate`` (the plate being promoted) AND a per-room manifest exists
    for its recipe_key; then the plate's painted set pieces must still sit on the authored logical cells
    (reprojected bbox + NCC template match). A DRIFT is a HARD rejection — the eval-blindness #1462 fixed.

    Returns {ran, passed, ...}. Non-blocking NO-OP (ran=False) when: no candidate_plate (today's
    nominations), no committed manifest for the room, or the qa image lane (Pillow/numpy) is absent in
    this interpreter — the standalone ci.yml `paint-drift-gate` job is the always-on enforcement; here
    we never crash a text-heavy batch on a missing optional dep. A real DRIFT, when the check DOES run,
    is loud (passed=False → the caller rejects)."""
    candidate = nom.get("candidate_plate")
    if not candidate:
        return {"ran": False, "passed": True, "reason": "no candidate_plate on nomination"}
    recipe_key = (nom.get("room_ref") or {}).get("recipe_key") or nom.get("room")
    try:
        import check_plate_drift as cpd  # noqa: PLC0415  (qa/ is on sys.path; PIL/numpy imported here)
    except Exception as e:  # pragma: no cover - depends on the host interpreter's image lane
        return {"ran": False, "passed": True, "reason": f"drift lane unavailable ({e})"}
    manifest_path = cpd._find_manifest_for_recipe(recipe_key, candidate, cpd._MANIFESTS_DIR)
    if manifest_path is None:
        return {"ran": False, "passed": True, "reason": f"no manifest for recipe_key {recipe_key!r}"}
    plate = Path(candidate)
    if not plate.is_absolute():
        plate = _REPO_ROOT / candidate
    if not plate.is_file():
        return {"ran": True, "passed": False, "reasons": [f"candidate_plate {candidate!r} not found"]}
    baseline = nom.get("baseline_plate")
    if baseline and not Path(baseline).is_absolute():
        baseline = str(_REPO_ROOT / baseline)
    res = cpd.check_plate_drift(plate, cpd.load_manifest(manifest_path), baseline=baseline)
    out = res.as_dict()
    out["ran"] = True
    return out


# ── Visual nomination promotion (the "room" strategy branch of promote_batch) ────────────────────
def _promote_visual(nom: dict, aid: str, *, registry: dict, library_dir: Path, promoted_at: str,
                    default_license: str, dry_run: bool, report: dict) -> None:
    """Gate + (on pass) write ONE visual nomination, mutating ``report`` with the same bookkeeping the
    text branch uses (promoted/rejected/skipped, details, entries, processed-log). The visual score is
    a PANEL JSON at the nomination's source_path — there is no score-if-unscored for visual (a plate is
    scored by the visual-critic panel upstream, not by promote.py), so a missing/unreadable panel is a
    score-failed skip, not a live-scoring attempt."""
    cls = nom.get("class")
    src = nom.get("source_path")
    if not src:
        report["skipped"] += 1
        report["details"].append({"artifact_id": aid, "verdict": "score-failed",
                                  "error": "visual nomination has no 'source_path' (panel JSON)"})
        if not dry_run:
            _append_processed(library_dir, aid, "score-failed", None)
        return
    try:
        panel = load_visual_panel(src)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        report["skipped"] += 1
        report["details"].append({"artifact_id": aid, "verdict": "score-failed",
                                  "error": f"cannot read visual panel {src!r}: {e}"})
        if not dry_run:
            _append_processed(library_dir, aid, "score-failed", None)
        return

    noise = float(registry.get("noise_law", 1.2))
    gate = evaluate_visual_gate(panel, registry=registry, noise_law=noise)
    # #1462 paint-drift HARD FLOOR: a room plate that slid a set piece off its authored cell is
    # rejected even if the taste/delta gate passed (that drift is what reads as "actors over the logs").
    drift = _paint_drift_gate(nom)
    passed = gate.passed and drift.get("passed", True)
    detail = {"artifact_id": aid, "verdict": "promoted" if passed else "rejected", **gate.as_dict()}
    if drift.get("ran"):
        detail["paint_drift"] = drift
    report["details"].append(detail)
    if not passed:
        report["rejected"] += 1
        if not dry_run:
            _append_processed(library_dir, aid, "rejected", None)
        return

    score_row = {"run_id": None, "world": nom.get("world") or panel.get("world") or "worldos",
                 "sha": None, "source_path": src, "panel_id": panel.get("panel_id"),
                 "ac_ruler": None, "class": cls}
    entry = build_entry(aid, cls, score_row, gate, license=nom.get("license") or default_license,
                        promoted_at=promoted_at, curation_note=nom.get("curation_note"),
                        payload=nom.get("payload"), room_ref=nom.get("room_ref"))
    report["promoted"] += 1
    if not dry_run:
        path = write_entry(library_dir, entry)
        report["entries"].append(str(path))
        _append_processed(library_dir, aid, "promoted", gate.tier)
    else:
        report["entries"].append(f"(dry-run) {cls}/{aid}")


# ── The batch driver ────────────────────────────────────────────────────────────────────────────
def promote_batch(
    *,
    library_dir: Path | str = DEFAULT_LIBRARY_DIR,
    nominations_path: Path | str = DEFAULT_NOMINATIONS,
    db_path: Path | str = scores_db.DB_PATH,
    default_license: str = DEFAULT_LICENSE,
    skip_unscored: bool = False,
    dry_run: bool = False,
    budget: str = "1.50",
) -> dict:
    """Process every unprocessed nomination top-to-bottom. Returns a batch report dict.

    Idempotent: an artifact_id in the processed-log is skipped. Exits-0 semantics live in main(); this
    returns a report. On ``dry_run`` NOTHING is written (no library entries, no processed-log, no
    scoring) — it is a pure gate preview. On ``skip_unscored`` an unscored nomination is left for a
    later live batch (recorded as processed=skipped so the queue drains but not the library)."""
    library_dir = Path(library_dir)
    noms = read_nominations(nominations_path)
    processed = read_processed(library_dir) if not dry_run else set()
    rows_by_id = _artifacts_by_id(db_path)
    all_rows = scores_db.fetch_artifacts(db_path)
    visual_registry = load_visual_registry()  # cheap; empty registry if the file is absent (fail-closed)
    promoted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    report: dict[str, Any] = {
        "nominations": len(noms), "promoted": 0, "rejected": 0, "skipped": 0,
        "already_processed": 0, "dry_run": dry_run, "entries": [], "details": [],
    }

    if not dry_run and noms:
        ensure_pack(library_dir, license=default_license)

    for nom in noms:
        aid = nom["artifact_id"]
        if aid in processed:
            report["already_processed"] += 1
            continue
        # Mark this artifact_id handled for the REST of this batch, not just future batches — a
        # nominations.jsonl with a duplicate artifact_id in the same file must not be scored/promoted
        # twice within one run (the on-disk processed-log only guards the NEXT run).
        processed.add(aid)

        # GATE_STRATEGIES dispatch: a visual class ("room") takes the delta-anchored visual gate,
        # which reads its panel JSON at source_path (NOT the `artifacts` DB table). Text classes fall
        # through to the byte-unchanged DB-backed path below.
        if _strategy_for(nom) == "visual":
            _promote_visual(nom, aid, registry=visual_registry, library_dir=library_dir,
                            promoted_at=promoted_at, default_license=default_license,
                            dry_run=dry_run, report=report)
            continue

        row = rows_by_id.get(aid)
        if row is None or row.get("overall") is None:
            # unscored
            if skip_unscored or dry_run:
                report["skipped"] += 1
                report["details"].append({"artifact_id": aid, "verdict": "skipped-unscored"})
                if not dry_run:
                    _append_processed(library_dir, aid, "skipped-unscored", None)
                continue
            try:
                row = score_if_unscored(nom, budget=budget, db_path=db_path)
            except Exception as e:  # noqa: BLE001 — score-if-unscored must never abort the --batch run
                report["skipped"] += 1
                report["details"].append(
                    {"artifact_id": aid, "verdict": "score-failed", "error": str(e)})
                if not dry_run:
                    _append_processed(library_dir, aid, "score-failed", None)
                continue
            if row is None or row.get("overall") is None:
                report["skipped"] += 1
                report["details"].append({"artifact_id": aid, "verdict": "score-failed"})
                _append_processed(library_dir, aid, "score-failed", None)
                continue
            all_rows = scores_db.fetch_artifacts(db_path)  # refresh: new panel rows landed

        cv = control_valid_for_panel(row.get("panel_id"), all_rows)
        gate = evaluate_gate(row, control_valid=cv)
        detail = {"artifact_id": aid, "verdict": "promoted" if gate.passed else "rejected",
                  **gate.as_dict()}
        report["details"].append(detail)

        if not gate.passed:
            report["rejected"] += 1
            if not dry_run:
                _append_processed(library_dir, aid, "rejected", None)
            continue

        cls = row["class"]
        entry = build_entry(
            aid, cls, row, gate, license=nom.get("license") or default_license,
            promoted_at=promoted_at, curation_note=nom.get("curation_note"),
            payload=nom.get("payload"), room_ref=nom.get("room_ref"),
        )
        report["promoted"] += 1
        if not dry_run:
            path = write_entry(library_dir, entry)
            report["entries"].append(str(path))
            _append_processed(library_dir, aid, "promoted", gate.tier)
        else:
            report["entries"].append(f"(dry-run) {cls}/{aid}")

    return report


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", action="store_true", required=True,
                    help="process every unprocessed nomination (the only v1 mode)")
    ap.add_argument("--library", default=str(DEFAULT_LIBRARY_DIR), help="library/ pack root")
    ap.add_argument("--nominations", default=str(DEFAULT_NOMINATIONS), help="the nomination queue")
    ap.add_argument("--db", default=str(scores_db.DB_PATH), help="path to scores.db")
    ap.add_argument("--license", default=DEFAULT_LICENSE, help="default entry license (SPDX-ish)")
    ap.add_argument("--skip-unscored", action="store_true",
                    help="promote only already-scored nominations; leave unscored for a live batch")
    ap.add_argument("--dry-run", action="store_true",
                    help="gate preview only — write NOTHING (no scoring, no library, no processed-log)")
    ap.add_argument("--budget", default="1.50", help="per-scorer USD budget for score-if-unscored")
    args = ap.parse_args(argv)

    report = promote_batch(
        library_dir=Path(args.library), nominations_path=Path(args.nominations), db_path=args.db,
        default_license=args.license, skip_unscored=args.skip_unscored, dry_run=args.dry_run,
        budget=args.budget,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0  # batch always exits 0, even with zero promotions (CLI contract)


if __name__ == "__main__":
    raise SystemExit(main())
