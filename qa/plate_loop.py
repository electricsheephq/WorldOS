#!/usr/bin/env python3
"""plate_loop.py — the PLATE SPRINT one-command generate -> gate -> score -> gallery harness.

The overnight work never ran the visible generate->look->score->iterate loop because that loop only
existed as SCATTERED pieces (generate_room.py, qa/plate_overlays.py edge-recall, qa/check_plate_drift.py
NCC, qa/visual_pregate.py G1/G6, the visual-critic panel recipe, qa/scores_db.py). This module is the
missing conductor: ONE command per candidate config that generates the plate, runs the deterministic
REGISTRATION + PRE-GATES, PREPARES the blind scoring panel, and appends a row to an owner-visible HTML
gallery contact sheet — then, once the orchestrator has run the panel, ingests the verdict and finishes
the row.

★ THE PANEL IS AGENT-WORK, NOT SCRIPT-WORK — the load-bearing boundary
--------------------------------------------------------------------
This script NEVER calls an LLM. The 5-scorer blind panel is run by the ORCHESTRATOR (5 subagents, the
visual-critic SKILL recipe). plate_loop.py only:
  * STAGES the panel packet into <out-dir>/panel/ — image A (candidate), image B (incumbent canonical),
    image C (disguised real-art control), each copied to a blind slot name, plus prompts.json (the
    scoring rubric text) and a disclosed house-best reference for the house-style question;
  * writes the blind slot->A/B/C mapping OUTSIDE the panel dir (scorers Read adjacent files — the map
    must not be adjacent), and records the run state in <out-dir>/panel/contract.json;
  * PRINTS the invocation contract (how to run the scorers + how to finish the row).
The orchestrator runs the scorers, assembles a verdict JSON (the committed
qa/evidence/cohesion-probe/panel_verdict.json shape), and re-invokes:
    python3 qa/plate_loop.py --panel-verdict <verdict.json> --out-dir <dir> --gallery <html>
which ingests the medians, writes the scores_db row (surface="visual"), and updates the gallery.

TWO-PHASE FLOW
--------------
  # Phase 1 — generate + deterministic gates + stage the panel + append the (panel-pending) gallery row
  python3 qa/plate_loop.py --room crypt --config cfg.json --out-dir out/ --gallery gallery.html
  # ... orchestrator runs the 5 blind scorers per <out-dir>/panel/prompts.json ...
  # Phase 2 — ingest the completed verdict, finish the scores_db row + gallery row
  python3 qa/plate_loop.py --panel-verdict verdict.json --out-dir out/ --gallery gallery.html

CONFIG SCHEMA (JSON) — designed now, tolerant of the pieces the ARM lanes will add
----------------------------------------------------------------------------------
  {
    "name": "armA-crypt-cn-depth-stylepass-0.35",   # row id (stable; a re-run upserts, never dups)
    "room": "crypt",                                  # room key (overridable by --room)
    "generate": {                                     # -> generate_room.py flags (existing surface)
      "base_plate": "/abs/greybox.png",               # --base-plate
      "strength": 0.30,                               # --strength
      "seed": 12345,                                  # --seed
      "controlnet": "depth",                          # --controlnet depth|canny  (registration lane)
      "control_strength": 0.7,                        # --control-strength
      "control_model": "model_bfl-flux-1-dev",        # --control-model
      "layered": false,                               # --layered
      "extra_flags": ["--lighting", "firelit"],       # verbatim passthrough escape hatch
      "candidate": "/abs/already_generated.png"       # OPTIONAL: skip generation, use this plate
    },
    "style_pass": {                                   # ★ FORWARD-LOOKING (ARM A). Absent today == fine.
      "model": "model_z-image",                       # a post-base z-image+painterly-LoRA img2img pass
      "loras": ["model_MB22..."],                     # forwarded to generate_room as --style-pass <json>
      "strength": 0.35                                # (generate_room gains --style-pass in ARM A; when
    },                                                #  style_pass is present the config OWNS that dep)
    "registration": {
      "greybox": "/abs/greybox.png",                  # control image for edge-alignment recall
      "min_recall": 0.95,                             # gate threshold (default 0.95)
      "manifest": "qa/room_manifests/crypt_dense_v1.cells.json",  # optional NCC drift (when present)
      "baseline": "/abs/known_good.png"               # optional --baseline for the NCC fingerprint
    },
    "panel": {
      "incumbent": "/abs/crypt_dense_v1.jpg",         # B — the incumbent canonical plate
      "control": "/abs/poe2_ruins_control.jpg",       # C — a disguised shipped real-art control
      "house_anchor": "/abs/crypt_dense_v1.jpg"       # disclosed "house best" for the house-style Q
    }
  }
The style_pass block is TOLERATED-ABSENT: the common config omits it and plate_loop takes the plain
generate path. When it IS present, plate_loop forwards it as `--style-pass <tmp.json>` to
generate_room.py — the ARM A lane wires that flag; a config carrying style_pass declares that dependency.

Deterministic + offline except (a) the optional generate_room.py subprocess (Scenario API) and (b) the
scores_db write in phase 2. The unit tests (qa/test_plate_loop.py) exercise the registration math,
gallery-append idempotence, and config parsing with a tiny synthetic fixture — NO API calls.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

# generate_room.py lives in the godot renderer tools; its output plate is what we gate.
_GENERATE_ROOM = _QA_DIR.parent / "extensions" / "renderers" / "godot" / "tools" / "generate_room.py"

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class PlateConfig:
    name: str
    room: str
    generate: dict = field(default_factory=dict)
    style_pass: Optional[dict] = None
    registration: dict = field(default_factory=dict)
    panel: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def run_id(self) -> str:
        return _slug(self.name)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s)).strip("-") or "plate-run"


def load_config(path: str | Path, *, room_override: Optional[str] = None) -> PlateConfig:
    """Parse a plate-loop config JSON into a PlateConfig. Raises ValueError on a missing room (the one
    field with no safe default) so a typo fails loud rather than generating the wrong room."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    room = room_override or raw.get("room")
    if not room:
        raise ValueError("config needs a 'room' (or pass --room)")
    name = raw.get("name") or f"{room}-{_now_stamp()}"
    style_pass = raw.get("style_pass")
    if style_pass is not None and not isinstance(style_pass, dict):
        raise ValueError("'style_pass' must be an object {model, loras, strength} when present")
    return PlateConfig(
        name=str(name),
        room=str(room),
        generate=dict(raw.get("generate") or {}),
        style_pass=style_pass,
        registration=dict(raw.get("registration") or {}),
        panel=dict(raw.get("panel") or {}),
        raw=raw,
    )


def config_summary(cfg: PlateConfig) -> str:
    """A compact one-line human summary of what makes this config distinct (shown in the gallery)."""
    g = cfg.generate
    parts: list[str] = []
    if g.get("controlnet"):
        cs = g.get("control_strength")
        parts.append(f"cn={g['controlnet']}" + (f"@{cs}" if cs is not None else ""))
    if g.get("control_model"):
        parts.append(f"model={g['control_model']}")
    if g.get("strength") is not None:
        parts.append(f"str={g['strength']}")
    if g.get("layered"):
        parts.append("layered")
    if g.get("seed") is not None:
        parts.append(f"seed={g['seed']}")
    if cfg.style_pass:
        sp = cfg.style_pass
        parts.append(f"style={sp.get('model', '?')}@{sp.get('strength', '?')}")
    for f in g.get("extra_flags") or []:
        parts.append(str(f))
    return " ".join(parts) or "(defaults)"


# ---------------------------------------------------------------------------
# Generation (optional subprocess — the only API-side step in phase 1)
# ---------------------------------------------------------------------------
def build_generate_argv(cfg: PlateConfig, out_dir: Path, *, style_pass_file: Optional[Path] = None) -> list[str]:
    """Build the generate_room.py argv from the config's `generate` block (the EXISTING flag surface),
    plus a forwarded `--style-pass <json>` when the config carries a style_pass block (ARM A). Pure —
    no side effects except (optionally) reading style_pass; unit-tested directly."""
    g = cfg.generate
    argv = ["--room", cfg.room, "--out", str(out_dir)]
    if g.get("base_plate"):
        argv += ["--base-plate", str(g["base_plate"])]
    if g.get("refine_from"):
        argv += ["--refine-from", str(g["refine_from"])]
    if g.get("strength") is not None:
        argv += ["--strength", str(g["strength"])]
    if g.get("seed") is not None:
        argv += ["--seed", str(g["seed"])]
    if g.get("controlnet"):
        argv += ["--controlnet", str(g["controlnet"])]
        if g.get("control_strength") is not None:
            argv += ["--control-strength", str(g["control_strength"])]
        if g.get("control_model"):
            argv += ["--control-model", str(g["control_model"])]
    if g.get("layered"):
        argv += ["--layered"]
    for extra in g.get("extra_flags") or []:
        argv.append(str(extra))
    if style_pass_file is not None:
        argv += ["--style-pass", str(style_pass_file)]
    return argv


def _newest_image(under: Path, *, exclude: set[str] | None = None) -> Optional[Path]:
    exclude = exclude or set()
    cands = [p for p in under.rglob("*")
             if p.is_file() and p.suffix.lower() in _IMAGE_EXTS and str(p.resolve()) not in exclude]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _declared_final_plate(gen_dir: Path) -> Optional[Path]:
    """generate_room's EXPLICITLY-declared final plate (style_pass / layered `final_plate` in
    scenario_meta.json), so the gate scores the same image the generator promoted — not merely the
    newest file by mtime. The style pass emits N samples and generate_room selects one (the most-
    stylized that still registers); relying on mtime could gate a different, rejected sample. Returns
    None when there is no meta / no declared plate (plain single-output runs) so the caller falls back
    to newest-image."""
    meta_path = gen_dir / "scenario_meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for block in ("style_pass", "layered"):
        path = ((meta.get(block) or {}).get("final_plate") or {}).get("path")
        if path and Path(path).is_file():
            return Path(path)
    return None


def run_generate(cfg: PlateConfig, out_dir: Path, *, dry_run: bool = False) -> Path:
    """Run generate_room.py into <out-dir>/gen/ and return the generated plate path.

    Skips generation and returns the given plate when `generate.candidate` (or a passed candidate) is
    set. Forwards a style_pass block as a temp `--style-pass` json. Raises on a failed subprocess or a
    missing output image (fail loud — a silent 'no plate' would poison every downstream gate)."""
    gen_dir = out_dir / "gen"
    gen_dir.mkdir(parents=True, exist_ok=True)
    style_pass_file: Optional[Path] = None
    if cfg.style_pass:
        style_pass_file = out_dir / "style_pass.json"
        style_pass_file.write_text(json.dumps(cfg.style_pass, indent=2), encoding="utf-8")
    argv = build_generate_argv(cfg, gen_dir, style_pass_file=style_pass_file)
    cmd = [sys.executable, str(_GENERATE_ROOM), *argv]
    if dry_run:
        print("[plate_loop] DRY-RUN generate:", " ".join(cmd))
        return gen_dir / "(dry-run-no-plate)"
    before = {str(p.resolve()) for p in gen_dir.rglob("*") if p.is_file()}
    print("[plate_loop] generate:", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"generate_room.py failed (exit {proc.returncode}); see output above")
    # Prefer the generator's declared final plate (the selected style/layered sample) over newest-mtime.
    declared = _declared_final_plate(gen_dir)
    plate = declared or _newest_image(gen_dir, exclude=before)
    if plate is None:
        raise RuntimeError(f"generate_room.py produced no image under {gen_dir}")
    if declared is not None:
        print(f"[plate_loop] using generate_room's declared final plate: {plate}")
    return plate


# ---------------------------------------------------------------------------
# Registration gate (edge-recall + optional NCC drift)
# ---------------------------------------------------------------------------
def registration_gate(candidate: Path, cfg: PlateConfig) -> dict:
    """Deterministic registration gate. Primary signal = edge-alignment recall vs the room greybox
    (>= min_recall passes). Secondary = check_plate_drift NCC when a room manifest is configured (a
    prop that slid off its authored cell fails). Returns a JSON-able dict; `passed` requires the recall
    gate AND (drift passed or was not runnable)."""
    reg = cfg.registration
    greybox = reg.get("greybox") or cfg.generate.get("base_plate")
    min_recall = float(reg.get("min_recall", 0.95))
    out: dict[str, Any] = {"min_recall": min_recall, "recall": None, "recall_pass": None,
                           "drift": None, "passed": False, "notes": []}
    if not greybox or not Path(greybox).is_file():
        out["notes"].append("no greybox/base_plate available — registration UNGATED (recall skipped)")
        # Without a control image we cannot measure recall; do not falsely pass.
        return out
    from plate_overlays import registration_recall  # lazy: PIL only
    recall_val = registration_recall(greybox, candidate)
    out["recall"] = round(recall_val, 4)
    out["recall_pass"] = recall_val >= min_recall

    manifest_path = reg.get("manifest")
    if manifest_path and Path(manifest_path).is_file():
        try:
            import check_plate_drift as drift  # lazy: numpy + PIL
            res = drift.check_plate_drift(str(candidate), drift.load_manifest(manifest_path),
                                          baseline=reg.get("baseline"))
            out["drift"] = res.as_dict()
        except Exception as exc:  # never let an NCC hiccup mask the recall verdict
            out["notes"].append(f"drift check errored ({exc}); recall verdict stands")
    drift_ok = out["drift"] is None or bool(out["drift"].get("passed", True))
    out["passed"] = bool(out["recall_pass"]) and drift_ok
    return out


# ---------------------------------------------------------------------------
# Pre-gates (G1 frame-lit + G6 luma-staging via the existing visual_pregate)
# ---------------------------------------------------------------------------
def pregate(candidate: Path) -> dict:
    """Run the deterministic image-only pre-gates (G1 frame-lit + G6 luma-staging-law) on the plate.
    Returns {verdict, blocking, gates, summary}. No scenegrid/actors here — a backdrop PLATE has no
    actors yet; the actor-grounding gates (G3/G4) run later on the composed frame."""
    import visual_pregate as vp  # lazy: optional Pillow
    return vp.run_pregates(str(candidate))


# ---------------------------------------------------------------------------
# Panel packet preparation (the AGENT-work boundary: stage, don't score)
# ---------------------------------------------------------------------------
_PANEL_RUBRIC = (
    "Score the CANDIDATE plate's painterly quality + character-in-scene readiness as the GAP to the "
    "reference bar (Pillars of Eternity II: Deadfire). Score what you SEE — judge the image on its "
    "craft, NOT on assumptions about how it was made. Do NOT apply any 'AI-made deserves less' prior; "
    "harshness belongs in flaw-finding, not in a scale-suppressing prior. 0-10: 9-10 indistinguishable "
    "from the reference bar; 7-8 clearly the same world, minor tells; 5-6 reads as a game but visibly "
    "below the bar; 3-4 the illusion is breaking; 0-2 broken. Within-panel comparisons only."
)
_HOUSE_STYLE_QUESTION = (
    "Does the candidate read as the SAME painterly hand / hit the SAME craft bar as the disclosed "
    "house-best reference (house_best.*), or does it look like a different, lesser pipeline? A "
    "candidate that beats its real-art control but LOSES this house-style read is a REGRESSION, not "
    "an adoption — regardless of the absolute number."
)


def prepare_panel(candidate: Path, cfg: PlateConfig, out_dir: Path, *,
                  n_scorers: int = 5, control_band: float = 1.2) -> dict:
    """Stage the blind panel packet into <out-dir>/panel/ and return the contract dict.

    Images: A=candidate, B=incumbent canonical, C=disguised real-art control — each copied to a blind
    slot name (slot order shuffled deterministically from the run id, so the mapping is stable for a
    given run but reveals nothing). The house-best reference is copied in DISCLOSED (named house_best.*)
    for the house-style question. The slot->A/B/C mapping is written to <out-dir>/panel_mapping.json
    (OUTSIDE panel/, so a scorer reading files adjacent to the images never sees it)."""
    panel_dir = out_dir / "panel"
    panel_dir.mkdir(parents=True, exist_ok=True)

    p = cfg.panel
    sources = {"A": Path(candidate)}
    if p.get("incumbent"):
        sources["B"] = Path(p["incumbent"])
    if p.get("control"):
        sources["C"] = Path(p["control"])

    # Deterministic blind shuffle from the run id (stable per run, uninformative to the scorer).
    labels = list(sources.keys())
    rng = random.Random(hashlib.sha256(cfg.run_id.encode()).hexdigest())
    slots = [f"image_{i+1}" for i in range(len(labels))]
    shuffled = labels[:]
    rng.shuffle(shuffled)
    slot_to_label = dict(zip(slots, shuffled))          # image_N -> A/B/C
    label_to_slot = {v: k for k, v in slot_to_label.items()}

    staged: dict[str, str] = {}
    for slot, label in slot_to_label.items():
        src = sources[label]
        dst = panel_dir / f"{slot}{src.suffix.lower() if src.suffix else '.png'}"
        if src.is_file():
            shutil.copyfile(src, dst)
        staged[slot] = dst.name

    # Disclosed house-best reference (the house-style anchor — NOT blind).
    house = p.get("house_anchor") or p.get("incumbent")
    house_ref = None
    if house and Path(house).is_file():
        hp = Path(house)
        house_dst = panel_dir / f"house_best{hp.suffix.lower() if hp.suffix else '.png'}"
        shutil.copyfile(hp, house_dst)
        house_ref = house_dst.name

    prompts = {
        "task": "blind painterly-plate panel — score each image, then answer the house-style question",
        "rubric": _PANEL_RUBRIC,
        "house_style_question": _HOUSE_STYLE_QUESTION,
        "house_best_reference": house_ref,
        "images": sorted(staged.keys()),
        "n_scorers": n_scorers,
        "control_band": control_band,
        "instructions": (
            "Independently score EVERY image_N in this directory 0-10 on the rubric. Return TEXT-ONLY "
            "JSON: {\"scores\": {\"image_1\": N, ...}, \"ranking\": [...best->worst...], "
            "\"house_style\": {\"image_N\": \"same-hand|lesser\", ...}, \"notes\": \"...\"}. "
            f"Run {n_scorers} independent scorers; the harness takes the per-image MEDIAN. The "
            "real-art control's median sets the bar — a candidate at/above it (within the ±"
            f"{control_band} panel noise band) meets the bar."
        ),
    }
    (panel_dir / "prompts.json").write_text(json.dumps(prompts, indent=2), encoding="utf-8")
    # Mapping lives OUTSIDE panel/ (scorers Read adjacent files — keep the key non-adjacent).
    (out_dir / "panel_mapping.json").write_text(
        json.dumps({"slot_to_label": slot_to_label, "label_to_slot": label_to_slot,
                    "labels": {"A": "candidate", "B": "incumbent-canonical",
                               "C": "disguised-real-art-control"}}, indent=2),
        encoding="utf-8",
    )
    return {
        "panel_dir": str(panel_dir), "staged": staged, "slot_to_label": slot_to_label,
        "label_to_slot": label_to_slot, "house_best": house_ref, "n_scorers": n_scorers,
        "control_band": control_band,
    }


def invocation_contract(out_dir: Path, gallery: Path, panel: dict) -> str:
    """The human-readable PANEL-READY contract printed at the end of phase 1."""
    panel_dir = panel["panel_dir"]
    lines = [
        "",
        "── PANEL READY (agent-work — the harness does NOT call the LLM) ─────────────────────────────",
        f"  Packet: {panel_dir}/  (blind images + prompts.json)",
        f"  Run {panel['n_scorers']} INDEPENDENT blind scorers (visual-critic recipe, sonnet), each",
        f"  reading {panel_dir}/prompts.json + the image_N files; take the per-image MEDIAN.",
        "  Assemble a verdict JSON (qa/evidence/cohesion-probe/panel_verdict.json shape):",
        '    {"scores": {"A": [..5..], "B": [..], "C": [..]}, "medians": {"A":_,"B":_,"C":_},',
        '     "control_valid": true, "verdict": "<one-line read>"}',
        "  (Score keys may be the blind slot names — the harness maps them back via panel_mapping.json.)",
        "  Then FINISH the row:",
        f"    python3 qa/plate_loop.py --panel-verdict <verdict.json> --out-dir {out_dir} --gallery {gallery}",
        "────────────────────────────────────────────────────────────────────────────────────────────",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Panel verdict ingest (phase 2)
# ---------------------------------------------------------------------------
def _median(vals: list[float]) -> Optional[float]:
    xs = sorted(float(v) for v in vals if v is not None)
    if not xs:
        return None
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def ingest_verdict(verdict: dict, contract: dict) -> dict:
    """Normalise a panel verdict into per-label medians A/B/C + a control-anchored read.

    Accepts medians directly, or per-image score lists (keyed by A/B/C OR by the blind slot names,
    which are mapped back via the contract's slot_to_label). Returns
    {medians, control_valid, delta_vs_control, verdict}."""
    label_of = contract.get("slot_to_label", {})  # image_N -> A/B/C

    def _relabel(d: dict) -> dict:
        out: dict[str, Any] = {}
        for k, v in d.items():
            out[label_of.get(k, k)] = v
        return out

    medians = verdict.get("medians")
    if isinstance(medians, dict):
        medians = _relabel(medians)
    else:
        scores = _relabel(verdict.get("scores") or {})
        medians = {k: _median(v if isinstance(v, list) else [v]) for k, v in scores.items()}

    ctrl = medians.get("C")
    cand = medians.get("A")
    delta = (cand - ctrl) if (cand is not None and ctrl is not None) else None
    control_valid = verdict.get("control_valid")
    if control_valid is None and ctrl is not None:
        control_valid = True  # a control was scored; validity band is a panel-review concern
    return {
        "medians": {k: (round(v, 3) if v is not None else None) for k, v in medians.items()},
        "control_valid": control_valid,
        "delta_vs_control": round(delta, 3) if delta is not None else None,
        "verdict": verdict.get("verdict", ""),
    }


def write_scores_row(row: dict, panel_read: dict, *, db_path: str | Path | None = None) -> str:
    """Append ONE surface="visual" row to the canonical scores ledger for a completed panel.

    visual_overall = the candidate's panel median; notes carry the registration recall + pre-gate
    verdict + control delta. Returns the run_id written. Kept out of the unit tests' default path by
    accepting an explicit db_path (tests pass a tmp db; the real run uses qa/scores.db)."""
    import scores_db
    run_id = f"plate-{row['run_id']}-{_now_stamp()}"
    reg = row.get("registration") or {}
    medians = panel_read.get("medians") or {}
    cand_median = medians.get("A")
    note = (
        f"plate_loop {row['config_name']} · room={row['room']} · "
        f"registration recall={reg.get('recall')} (min {reg.get('min_recall')}, "
        f"{'PASS' if reg.get('passed') else 'FAIL'}) · pregate={row.get('pregate', {}).get('verdict')} · "
        f"panel medians A/B/C={medians.get('A')}/{medians.get('B')}/{medians.get('C')} · "
        f"delta_vs_control={panel_read.get('delta_vs_control')} · "
        f"control_valid={panel_read.get('control_valid')} · {panel_read.get('verdict', '')}"
    ).strip()
    kwargs: dict[str, Any] = dict(
        surface="visual", scorer_model="sonnet",
        methodology="plate_loop blind panel (visual-critic recipe, 5 scorers)",
        visual_scene=row["room"], visual_backend="still",
        visual_pregate=row.get("pregate", {}).get("verdict"),
        visual_overall=cand_median, notes=note, source_path=row.get("out_dir"),
    )
    if db_path is None:
        scores_db.add_run(run_id=run_id, **kwargs)
    else:
        scores_db.add_run(run_id=run_id, db_path=db_path, **kwargs)
    return run_id


# ---------------------------------------------------------------------------
# Gallery (the owner-visible contact sheet — idempotent upsert by run id)
# ---------------------------------------------------------------------------
def _rows_path(gallery: Path) -> Path:
    return gallery.with_suffix(gallery.suffix + ".rows.json")


def load_rows(gallery: Path) -> list[dict]:
    rp = _rows_path(gallery)
    if not rp.is_file():
        return []
    try:
        data = json.loads(rp.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def append_gallery_row(gallery: str | Path, row: dict) -> list[dict]:
    """UPSERT a row (keyed by row['id']) into the gallery's sidecar store, newest-first, then re-render
    the HTML. Idempotent by construction: appending the same id twice replaces (never duplicates) —
    which is exactly what phase 2 needs when it finishes a phase-1 row."""
    gallery = Path(gallery)
    gallery.parent.mkdir(parents=True, exist_ok=True)
    rid = row.get("id") or _slug(row.get("config_name", "plate-run"))
    row["id"] = rid
    rows = [r for r in load_rows(gallery) if r.get("id") != rid]
    rows.insert(0, row)  # newest first
    rows.sort(key=lambda r: r.get("ts") or "", reverse=True)
    _rows_path(gallery).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    gallery.write_text(render_gallery_html(rows), encoding="utf-8")
    return rows


def _thumb_data_uri(image: Path, *, max_px: int = 360) -> Optional[str]:
    """A small base64 JPEG data URI of the plate so the gallery HTML is self-contained (no broken
    relative paths). Returns None when Pillow is unavailable or the image can't be read."""
    try:
        from PIL import Image  # type: ignore
        im = Image.open(image).convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return html.escape(str(v))


def _row_card(r: dict) -> str:
    reg = r.get("registration") or {}
    pre = r.get("pregate") or {}
    panel = r.get("panel") or {}
    medians = (panel.get("medians") or {}) if isinstance(panel, dict) else {}
    thumb = r.get("thumbnail")
    img_html = (f'<img src="{thumb}" alt="{html.escape(r.get("config_name", ""))}">'
                if thumb else '<div class="noimg">no thumbnail</div>')
    reg_pass = reg.get("passed")
    reg_cls = "pass" if reg_pass else ("fail" if reg_pass is False else "na")
    pre_verdict = pre.get("verdict")
    pre_cls = {"PASS": "pass", "FLAG": "fail"}.get(pre_verdict, "na")
    panel_cell = (f'A {_fmt(medians.get("A"))} · B {_fmt(medians.get("B"))} · '
                  f'C {_fmt(medians.get("C"))}' if medians else "panel pending")
    delta = panel.get("delta_vs_control") if isinstance(panel, dict) else None
    return f"""    <div class="card">
      <div class="thumb">{img_html}</div>
      <div class="meta">
        <div class="name">{html.escape(r.get("config_name", "—"))}</div>
        <div class="sub">{html.escape(r.get("room", ""))} · {html.escape(r.get("ts", ""))}</div>
        <div class="cfg">{html.escape(r.get("config_summary", ""))}</div>
        <div class="badges">
          <span class="badge {reg_cls}">registration {_fmt(reg.get("recall"))} / {_fmt(reg.get("min_recall"))}</span>
          <span class="badge {pre_cls}">pre-gate {_fmt(pre_verdict)}</span>
          <span class="badge {'pass' if delta is not None and delta >= 0 else 'na'}">panel {panel_cell}{'' if delta is None else f' (Δctrl {_fmt(delta)})'}</span>
        </div>
      </div>
    </div>"""


def render_gallery_html(rows: list[dict]) -> str:
    """Render the newest-first contact-sheet HTML from the row store (deterministic; self-contained)."""
    cards = "\n".join(_row_card(r) for r in rows) or '    <div class="empty">no runs yet</div>'
    stamp = _now_iso()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WorldOS Plate Sprint — gallery</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ background:#14141a; color:#e8e6e0; font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .lead {{ color:#9a978f; margin:0 0 20px; font-size:13px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:16px; }}
  .card {{ background:#1d1d25; border:1px solid #2c2c38; border-radius:10px; overflow:hidden; display:flex; flex-direction:column; }}
  .thumb {{ background:#0c0c10; aspect-ratio:1344/768; display:flex; align-items:center; justify-content:center; }}
  .thumb img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .noimg {{ color:#55545e; font-size:12px; }}
  .meta {{ padding:12px 14px; }}
  .name {{ font-weight:600; word-break:break-all; }}
  .sub {{ color:#8a8880; font-size:12px; margin:2px 0 8px; }}
  .cfg {{ color:#b9b6ae; font-size:12px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; margin-bottom:10px; word-break:break-word; }}
  .badges {{ display:flex; flex-wrap:wrap; gap:6px; }}
  .badge {{ font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid #3a3a48; background:#23232d; }}
  .badge.pass {{ border-color:#2f6b45; background:#173026; color:#8ff0b8; }}
  .badge.fail {{ border-color:#6b2f38; background:#301719; color:#f0a0a8; }}
  .badge.na {{ color:#9a978f; }}
  .empty, .lead a {{ color:#9a978f; }}
</style></head>
<body>
  <h1>WorldOS Plate Sprint — gallery</h1>
  <p class="lead">Newest first · {len(rows)} run(s) · rendered {stamp}. Each row: candidate thumbnail,
  config, registration (edge-recall vs greybox), deterministic pre-gate, and the blind panel medians
  (A=candidate · B=incumbent · C=real-art control) once scored.</p>
  <div class="grid">
{cards}
  </div>
</body></html>"""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def phase1(cfg: PlateConfig, out_dir: Path, gallery: Path, *,
           candidate_override: Optional[Path] = None, dry_run: bool = False) -> dict:
    """Generate -> registration gate -> pre-gates -> stage panel -> append (panel-pending) gallery row.
    Writes <out-dir>/panel/contract.json (phase-2 state) and returns the contract dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate = candidate_override or (
        Path(cfg.generate["candidate"]) if cfg.generate.get("candidate") else run_generate(cfg, out_dir, dry_run=dry_run))
    if dry_run and candidate_override is None and not cfg.generate.get("candidate"):
        print("[plate_loop] DRY-RUN: stopping before gates (no plate generated).")
        return {"dry_run": True}

    reg = registration_gate(candidate, cfg)
    pre = pregate(candidate)
    print(f"[plate_loop] registration recall={reg.get('recall')} "
          f"(min {reg.get('min_recall')}) -> {'PASS' if reg['passed'] else 'FAIL'}")
    print(f"[plate_loop] pre-gate {pre['verdict']}")

    panel = prepare_panel(candidate, cfg, out_dir)
    thumb = _thumb_data_uri(candidate)

    row = {
        "id": cfg.run_id, "ts": _now_iso(), "config_name": cfg.name, "room": cfg.room,
        "config_summary": config_summary(cfg), "candidate": str(candidate),
        "thumbnail": thumb, "registration": reg,
        "pregate": {"verdict": pre["verdict"], "blocking": [g.get("gate") for g in pre.get("blocking", [])]},
        "panel": None, "out_dir": str(out_dir),
    }
    append_gallery_row(gallery, row)

    contract = {
        "run_id": cfg.run_id, "config_name": cfg.name, "room": cfg.room, "candidate": str(candidate),
        "out_dir": str(out_dir), "gallery": str(gallery), "registration": reg,
        "pregate": row["pregate"], "config_summary": config_summary(cfg),
        "slot_to_label": panel["slot_to_label"], "panel": panel, "row": row,
    }
    (out_dir / "panel" / "contract.json").write_text(json.dumps(contract, indent=2), encoding="utf-8")
    print(invocation_contract(out_dir, gallery, panel))
    return contract


def phase2(verdict_path: Path, out_dir: Path, gallery: Optional[Path], *,
           db_path: str | Path | None = None) -> dict:
    """Ingest a completed panel verdict: write the scores_db row + finish the gallery row."""
    contract_path = out_dir / "panel" / "contract.json"
    if not contract_path.is_file():
        raise FileNotFoundError(f"no phase-1 contract at {contract_path} — run phase 1 first")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    verdict = json.loads(Path(verdict_path).read_text(encoding="utf-8"))
    panel_read = ingest_verdict(verdict, contract)
    gallery = gallery or Path(contract["gallery"])

    row = contract["row"]
    row["panel"] = panel_read
    row["ts"] = _now_iso()
    run_id = write_scores_row(
        {"run_id": contract["run_id"], "config_name": contract["config_name"], "room": contract["room"],
         "registration": contract["registration"], "pregate": contract["pregate"], "out_dir": str(out_dir)},
        panel_read, db_path=db_path)
    row["scores_run_id"] = run_id
    append_gallery_row(gallery, row)
    print(f"[plate_loop] ingested panel: medians={panel_read['medians']} "
          f"delta_vs_control={panel_read['delta_vs_control']} -> scores_db {run_id}")
    print(f"[plate_loop] gallery updated: {gallery}")
    return {"panel_read": panel_read, "scores_run_id": run_id}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Plate-sprint generate->gate->panel-prep->gallery harness (the panel itself is "
                    "agent-work; see the module docstring).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--room", default=None, help="room key (overrides the config's room)")
    ap.add_argument("--config", default=None, help="plate-loop config JSON (phase 1)")
    ap.add_argument("--out-dir", required=True, help="output dir for this candidate's artifacts")
    ap.add_argument("--gallery", default=None, help="HTML gallery contact sheet to append to")
    ap.add_argument("--candidate", default=None, help="use this already-generated plate (skip generate)")
    ap.add_argument("--panel-verdict", default=None, help="phase 2: ingest a completed panel verdict JSON")
    ap.add_argument("--dry-run", action="store_true", help="print the generate argv, do not call the API")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)

    if args.panel_verdict:
        gallery = Path(args.gallery) if args.gallery else None
        phase2(Path(args.panel_verdict), out_dir, gallery)
        return 0

    if not args.config:
        ap.error("phase 1 needs --config (or use --panel-verdict for phase 2)")
    if not args.gallery:
        ap.error("phase 1 needs --gallery")
    cfg = load_config(args.config, room_override=args.room)
    phase1(cfg, out_dir, Path(args.gallery),
           candidate_override=Path(args.candidate) if args.candidate else None,
           dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
