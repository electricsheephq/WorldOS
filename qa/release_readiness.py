#!/usr/bin/env python3
"""WorldOS Release Readiness Index (RRI 0-10) — roll up existing QA artifacts into ONE
release number with a HARD-GATE FLOOR (a failed gate caps the score; it is NOT a soft
average that hides a zero — mirrors the SCORECARD RED-cap discipline).

Pure reader of on-disk artifacts (never the live HTTP channel, which can corrupt):
  - <run>/run.json            (ui_playtest_app.sh)  -> part_a native #356 gate
  - <run>/score.json          (ui_playtest_score.py) -> intro flow, satisfaction, bugs, image_404s
  - <run>/network.ndjson or <run>/player/network.ndjson
                               (palette/playwright)  -> image-render rate (img 200 vs 404)
  - <story.json>/<mech.json>  (score.sh + rubrics)  -> story-craft / mechanical 1-5
  - --behavioral GREEN|RED    (assert_behavioral.py exit)
  - --ui-audit PASS|FAIL      (ui_audit_health.sh exit)
  - --palette-live true|false (a clean /session-surface read done by the CALLER, not here)
  - --handoff-json handoff.json (optional Mac-built app proof from qa/app_handoff_gate.py)
  - --support-preflight-json support_vm_preflight.json (required when VM persona evidence
    relies on --handoff-json for Mac-built app proof)

The two NEW signals the plan calls for — image-render-rate and palette-live — are computed
here (image rate from score.json/network.ndjson) and passed in (palette-live), so this stays
a pure disk reader.

image_render gate sources (signals.image_render_source):
  - "vm-network"  : per-run network.ndjson /image rows with UNEXPECTED outcomes exist ->
                    the REAL rate is computed and is authoritative (recorded unexpected
                    404s can never be papered over). Rows classed as DESIGNED degradation
                    by the viewer's X-Image-Outcome header (no-art/placeholder — the VM
                    serves no _private art and runs a null provider, so its /image
                    requests 404 by construction) are excluded from the denominator
                    (audit F11-1b); rows without the field count exactly as before.
  - "mac-handoff" : no per-run UNEXPECTED /image denominator exists AND a valid
                    --handoff-json at the same --build-sha proved
                    health.image_probe_ok:true + the private art root across every
                    required handoff gate. This is a representative built-app image
                    probe, NOT a render rate — weaker but real Mac evidence.
  - "none"        : neither source -> the gate stays a HARD FAIL with an evidence gap.

A score.json image_404s count with no success denominator is an EVIDENCE GAP, never a
fabricated 0.0 rate (audit F11-1a) — KNOWN unexpected 404s (image_404s_unexpected > 0)
hard-gap the gate; an unknown split gaps it only when no Mac handoff carries it.

Usage:
  release_readiness.py --runs <run-dir>[,<run-dir>...] \
      [--story story.json] [--mech mech.json] \
      [--behavioral GREEN|RED] [--ui-audit PASS|FAIL] [--palette-live true|false] \
      [--build-sha SHA] [--expected-personas newbie,veteran,...] [--handoff-json handoff.json]
      [--support-preflight-json support_vm_preflight.json]
      [--out qa/RRI.json] [--scorecard-row]

Targets for 10/10 (each dimension is a gate; all must hold on ONE build):
  native gate PASS · arc completed · cross-persona satisfaction >=7 & no give-up ·
  0 critical bugs · story >=4.3 · mech >=4.5 · behavioral GREEN · ui-audit PASS ·
  image-render >=95% · palette-live true

ADDITIVE Phase-3 capabilities (every one is opt-in; absent inputs == today's output):
  - LATENCY GATES (latency_s_per_beat / latency_coldopen): per-beat GENERATION budget
    from qa/latency_baseline.json, sourced from the SAME on-disk artifacts already read
    (a run's latency.json sidecar / a latency block in run.json / score.json). They gate
    ONLY when latency evidence is PRESENT and over budget; when latency is ABSENT the gate
    is a documented EVIDENCE-GAP SKIP (excluded from passed/total), never a new false fail —
    so every pre-existing RRI result is byte-identical.
  - --deterministic-only: evaluate ONLY the gates needing no live LLM/persona evidence
    (native_gate, ui_audit, image_render, palette_live, + latency when present) and mark the
    LLM/persona gates SKIPPED (not FAILED) — an early advisory "do the deterministic release
    gates hold?" signal. NEVER claims a release verdict (release_ready stays false).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


REQUIRED_RELEASE_PERSONAS = ["newbie", "veteran", "adversarial", "narrative", "optimizer"]
RELEASE_VERDICT_GATE = "full_five_persona_rri"

# --- ADDITIVE Phase-3 signal taxonomy -----------------------------------------
# The gates that depend on LIVE LLM / persona evidence (a real `claude -p` DM beat,
# a persona's self-reported satisfaction, a model-scored story/mech lens). In
# --deterministic-only mode these are SKIPPED (not failed) so CI / the agent get an
# early "do the DETERMINISTIC release gates hold?" signal with no live model run.
LLM_PERSONA_GATES = (
    "arc_completed",
    "cross_persona_sat",
    "no_give_up",
    "zero_critical",
    "story_craft",
    "mechanical",
    "behavioral",
)
# The deterministic complement (need no live LLM/persona evidence). The two latency
# gates are deterministic measurements and join this set ONLY when latency evidence
# is present (otherwise they are an evidence-gap skip, never a deterministic fail).
DETERMINISTIC_GATES = (
    "native_gate",
    "ui_audit",
    "image_render",
    "palette_live",
    # WS0: story_engagement is a DETERMINISTIC, snapshot-derived measurement (no live LLM) —
    # it joins this set ONLY when engagement evidence is present (else an evidence-gap skip,
    # never a deterministic fail), exactly like the latency gates.
    "story_engagement",
)
LATENCY_GATES = ("latency_s_per_beat", "latency_coldopen")

# WS0 — short fix hints printed beside each inert authored system in the ENGAGEMENT section
# (which engine tool the DM should reach for to make the dead system fire in a real beat).
_ENGAGEMENT_FIX_HINTS = {
    "companion_approval": "move a companion's regard (record_decision approval_tags / adjust_attitude)",
    "camp_downtime": "run a camp beat / long_rest in the multi-day arc (camp_scene / long_rest)",
    "quests_objectives": "complete an objective or quest (complete_objective / complete_quest)",
    "acts_advance": "advance the narrative arc past act 1 (drive the engine narrative_arc cursor)",
    "consequences_fired": "let the due consequence fire (advance_time past its trigger_day)",
    "factions_membership": "join the seeded faction (join_faction)",
    "faction_arc": "advance the joined faction's arc (advance_faction_arc) [BLOCKED spike — stays WARN]",
    "companion_quest_arc": "advance the companion quest arc (advance_companion_quest_arc) [BLOCKED spike — stays WARN]",
    "companion_agenda": "evaluate/fire the companion agenda (check_companion_arc)",
    "decisions_recorded": "record a callback-worthy choice (record_decision)",
}

# Default per-beat latency budget — overridden by qa/latency_baseline.json when present.
# Healthy ledger figures (qa/scores_ledger.md) are ~78 s/beat and ~157 cold-open; these
# defaults add headroom so routine scorer/host variance never trips the gate.
DEFAULT_LATENCY_BASELINE = {"s_per_beat_budget": 120.0, "coldopen_s_budget": 240.0}
LATENCY_BASELINE_PATH = Path(__file__).resolve().parent / "latency_baseline.json"
REQUIRED_HANDOFF_GATES = ["web_scripted_smoke", "built_app_scripted_smoke", "built_app_codex_playtest"]
REQUIRED_HANDOFF_EVIDENCE_KINDS = [
    "screenshots",
    "app_status_snapshots",
    "session_surface_snapshots",
    "moves",
    "provider_trace",
    "console_logs",
    "network_logs",
    "action_logs",
]
REQUIRED_SCORE_FIELDS = {
    "persona": "nonempty string",
    "completed_intro_flow": "boolean",
    "persona_satisfaction": "number",
    "gave_up": "boolean",
    "bug_reports_critical": "integer",
    "console_errors": "integer",
}
MIN_BUILD_SHA_MATCH_CHARS = 7
GATE_SPLIT_CONTRACT = {
    "deterministic_built_app_smoke": {
        "scope": "fast built-app wiring proof with deterministic provider",
        "release_verdict": False,
    },
    "short_real_provider_playtest": {
        "scope": "short built-app proof with a real provider and provider trace evidence",
        "release_verdict": False,
    },
    RELEASE_VERDICT_GATE: {
        "scope": "non-partial five-persona release readiness verdict",
        "release_verdict": True,
    },
}


def looks_like_path(value: str) -> bool:
    return Path(value).is_absolute() or value.startswith(("./", "../", "~")) or "/" in value or "\\" in value


def read_json(path: Path) -> dict:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def read_json_with_error(path: Path) -> tuple[dict, str]:
    if not path or not path.exists():
        return {}, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON: {exc.msg}"
    except OSError as exc:
        return {}, f"read failed: {exc}"
    if not isinstance(payload, dict):
        return {}, "JSON root is not an object"
    return payload, ""


def read_ndjson(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path or not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# Designed-degradation classes of the additive X-Image-Outcome response header
# (viewer/server.py _serve_image, audit F11-1b): `no-art` (no descriptor — the UI
# shows its silhouette ON PURPOSE) and `placeholder` (a payload-less descriptor,
# e.g. the null provider's placeholder). Network rows carrying these classes are
# EXCLUDED from the render-rate denominator — they are not render failures.
# Rows without the field (older captures) keep today's status-code behavior.
DESIGNED_IMAGE_OUTCOMES = {"no-art", "no_art", "placeholder"}


def _row_image_outcome(row: dict) -> str:
    return str(row.get("image_outcome") or "").strip().lower()


def image_render_rate(run: Path, score: dict) -> tuple[float, int, int, str, str]:
    """Fraction of image requests that returned bytes (not 404), over UNEXPECTED
    outcomes only. Returns (rate, ok, total, source, evidence_gap_detail).

    Prefers network.ndjson rows (200 vs 404 image responses); rows whose additive
    `image_outcome` field classes them as DESIGNED degradation (no-art/placeholder)
    are excluded from the denominator — the VM has no _private art and a null image
    provider, so every /image request 404s by construction and those 404s are NOT
    render failures (audit F11-1). Rows without the field count exactly as before.

    The score.json fallback carries a 404 COUNT with no success denominator. That can
    never be converted into a rate: a 404-only count with an unknown denominator is an
    EVIDENCE GAP (the 5th tuple element; total stays 0), never a fabricated 0.0 — the
    fabricated denominator is what kept the image_render gate structurally un-passable
    on the VM release lane and blocked the #762 mac-handoff source (audit F11-1a)."""
    network_paths = [run / "network.ndjson", run / "player" / "network.ndjson"]
    net_path = next((p for p in network_paths if p.exists()), network_paths[0])
    net = read_ndjson(net_path)
    img = [n for n in net if "/image" in str(n.get("url", ""))]
    counted = [n for n in img if _row_image_outcome(n) not in DESIGNED_IMAGE_OUTCOMES]
    if counted:
        ok = sum(1 for n in counted if int(n.get("status", 0) or 0) and int(n.get("status")) < 400)
        total = len(counted)
        return (ok / total if total else 1.0), ok, total, str(net_path), ""
    if img:
        # every recorded /image row was expectation-classed DESIGNED degradation —
        # zero unexpected failures by direct row evidence, but also no denominator
        # (total stays 0; the persona still needs mac-handoff coverage to pass).
        return 1.0, 0, 0, str(net_path), ""
    # fallback: score.json carries image_404s but not the success count
    f404 = int(score.get("image_404s", 0) or 0)
    if f404 == 0:
        return 1.0, 0, 0, str(run / "score.json"), ""
    # ui_playtest_score.py (header-aware capture) may have expectation-classed the
    # 404s already: all-designed 404s are clean degradation (still no denominator);
    # KNOWN unexpected 404s are a hard evidence gap that no handoff may paper over.
    unexpected = score.get("image_404s_unexpected")
    if isinstance(unexpected, int) and not isinstance(unexpected, bool):
        if unexpected == 0:
            return 1.0, 0, 0, str(run / "score.json"), ""
        return 0.0, 0, 0, str(run / "score.json"), (
            f"{unexpected} unexpected image 404s recorded but no denominator"
        )
    # unknown split (no header data) → evidence gap, never a fabricated denominator
    return 0.0, 0, 0, str(run / "score.json"), (
        f"{f404} image 404s recorded but no denominator"
    )


def _latency_float(value) -> Optional[float]:
    """Coerce a latency column to a positive float, or None when it is absent/NULL.

    latency_rollup.py writes NULL (None) for s_per_beat/coldopen_s when a run has no
    derivable beat — that is ABSENT evidence (skip the gate), never a fabricated 0.0
    that would silently pass. Booleans and non-numerics are also treated as absent."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    if f != f:  # NaN guard
        return None
    return f


def read_latency(run: Path, run_json: dict, score: dict) -> tuple[Optional[float], Optional[float], str]:
    """Read (s_per_beat, coldopen_s, source) from the same on-disk artifacts the rollup
    already reads — a run's ``latency.json`` sidecar first (the per-run ledger qa/release_gate.sh
    stamps into each persona run dir via ``qa/latency_rollup.py --stamp-into``, derived from the
    duo's per-beat transcripts; NOTE the runners themselves write the rollup to the TRANSCRIPT dir,
    so without that stamp this gate stays a dormant evidence-gap skip), then a ``latency`` block
    inside run.json, then top-level latency fields on run.json / score.json. ABSENT everywhere ->
    (None, None, "none"), which makes the latency gates a documented EVIDENCE-GAP/skip, never a
    new false fail."""
    sidecar = read_json(run / "latency.json")
    candidates: list[tuple[dict, str]] = [
        (sidecar, str(run / "latency.json")),
        (run_json.get("latency") if isinstance(run_json.get("latency"), dict) else {}, str(run / "run.json")),
        (run_json, str(run / "run.json")),
        (score, str(run / "score.json")),
    ]
    for blob, src in candidates:
        if not isinstance(blob, dict):
            continue
        s_per_beat = _latency_float(blob.get("s_per_beat"))
        coldopen_s = _latency_float(blob.get("coldopen_s"))
        if s_per_beat is not None or coldopen_s is not None:
            return s_per_beat, coldopen_s, src
    return None, None, "none"


def load_latency_baseline(path: Path = LATENCY_BASELINE_PATH) -> dict:
    """Per-beat latency BUDGET. Falls back to DEFAULT_LATENCY_BASELINE when the baseline
    file is absent or malformed (additive: a missing baseline never changes behavior)."""
    payload = read_json(path)
    out = dict(DEFAULT_LATENCY_BASELINE)
    for key in ("s_per_beat_budget", "coldopen_s_budget"):
        val = _latency_float(payload.get(key))
        if val is not None:
            out[key] = val
    return out


def split_csv(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def build_sha_matches(reported: str, expected: str) -> bool:
    reported = (reported or "").strip()
    expected = (expected or "").strip()
    if len(reported) < MIN_BUILD_SHA_MATCH_CHARS or len(expected) < MIN_BUILD_SHA_MATCH_CHARS:
        return False
    return reported == expected or reported.startswith(expected) or expected.startswith(reported)


def score_schema_errors(score: dict) -> list[str]:
    errors: list[str] = []
    persona = score.get("persona")
    if not isinstance(persona, str) or not persona.strip():
        errors.append("persona must be a nonempty string")
    if not isinstance(score.get("completed_intro_flow"), bool):
        errors.append("completed_intro_flow must be boolean")
    sat = score.get("persona_satisfaction")
    if not isinstance(sat, (int, float)) or isinstance(sat, bool):
        errors.append("persona_satisfaction must be numeric")
    if not isinstance(score.get("gave_up"), bool):
        errors.append("gave_up must be boolean")
    for field in ("bug_reports_critical", "console_errors"):
        value = score.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{field} must be integer")
    return errors


def resolve_manifest_path(handoff_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return handoff_path.parent / path


def resolve_evidence_file(manifest_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return manifest_path.parent / path


def handoff_gap(missing: str, detail: str) -> dict:
    return {"gate": "native_gate", "missing": missing, "detail": detail}


def support_preflight_gap(missing: str, detail: str) -> dict:
    return {"gate": "support_preflight", "missing": missing, "detail": detail}


def validate_support_preflight_json(preflight_json: str, expected_sha: str) -> tuple[dict, list[dict]]:
    proof = {
        "path": preflight_json or "",
        "valid": False,
        "schema": "",
        "verdict": "",
        "ready_for_rri": False,
        "release_verdict": False,
        "readiness": {},
    }
    if not preflight_json:
        return proof, []

    preflight_path = Path(preflight_json)
    payload, error = read_json_with_error(preflight_path)
    if error:
        return proof, [support_preflight_gap(preflight_json, f"support preflight JSON {error}")]

    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    repo = payload.get("repo") if isinstance(payload.get("repo"), dict) else {}
    rri_plan = payload.get("rri_plan") if isinstance(payload.get("rri_plan"), dict) else {}
    gaps: list[dict] = []
    proof.update(
        {
            "schema": payload.get("schema") or "",
            "verdict": payload.get("verdict") or "",
            "ready_for_rri": payload.get("ready_for_rri") is True,
            "release_verdict": bool(payload.get("release_verdict")),
            "readiness": {
                "safe_to_run_personas": readiness.get("safe_to_run_personas") is True,
                "same_sha_ready": readiness.get("same_sha_ready") is True,
                "provider": readiness.get("provider") or "",
                "player_agent": readiness.get("player_agent") or "",
                "provider_auth_ready": readiness.get("provider_auth_ready") is True,
                "player_agent_auth_ready": readiness.get("player_agent_auth_ready") is True,
                "required_tools_ready": readiness.get("required_tools_ready") is True,
                "persona_briefs_ready": readiness.get("persona_briefs_ready") is True,
                "private_art_ready": readiness.get("private_art_ready") is True,
                "artifact_return_ready": readiness.get("artifact_return_ready") is True,
                "host_capacity_ready": readiness.get("host_capacity_ready") is True,
                "mac_handoff_required": readiness.get("mac_handoff_required") is True,
                "blocking_categories": readiness.get("blocking_categories") if isinstance(readiness.get("blocking_categories"), list) else [],
                "expected_sha": readiness.get("expected_sha") or "",
                "repo_head_short": readiness.get("repo_head_short") or repo.get("head_short") or "",
                "min_memory_gb": readiness.get("min_memory_gb"),
            },
            "rri_plan": {
                "support_preflight_json": rri_plan.get("support_preflight_json") or "",
                "support_preflight_required_for_split_rollup": rri_plan.get("support_preflight_required_for_split_rollup") is True,
                "rri_rollup_command_template": rri_plan.get("rri_rollup_command_template") or "",
            },
        }
    )
    if payload.get("schema") != "worldos.support-vm-preflight.v1":
        gaps.append(support_preflight_gap(str(preflight_path), "support preflight schema is missing or wrong"))
    if payload.get("verdict") != "passed":
        gaps.append(support_preflight_gap(str(preflight_path), f"support preflight verdict is {payload.get('verdict') or 'missing'}"))
    if payload.get("ready_for_rri") is not True:
        gaps.append(support_preflight_gap(str(preflight_path), "support preflight ready_for_rri was not true"))
    if payload.get("release_verdict") is not False:
        gaps.append(support_preflight_gap(str(preflight_path), "support preflight must not claim a release verdict"))
    if repo.get("dirty") is not False:
        gaps.append(support_preflight_gap(str(preflight_path), "support preflight repo was dirty or unproven clean"))
    if repo.get("expected_sha_match") is not True:
        gaps.append(support_preflight_gap(str(preflight_path), "support preflight repo expected_sha_match was not true"))
    origin_main_query = repo.get("origin_main_query") if isinstance(repo.get("origin_main_query"), dict) else {}
    if origin_main_query.get("ok") is not True:
        gaps.append(support_preflight_gap(str(preflight_path), "support preflight origin/main query was not proven"))
    if expected_sha:
        preflight_sha = str(readiness.get("expected_sha") or repo.get("expected_sha") or repo.get("head_short") or "")
        repo_head = str(readiness.get("repo_head_short") or repo.get("head_short") or "")
        if not build_sha_matches(preflight_sha, expected_sha):
            gaps.append(support_preflight_gap(str(preflight_path), f"support preflight expected_sha {preflight_sha or 'missing'} does not match --build-sha {expected_sha}"))
        if not build_sha_matches(repo_head, expected_sha):
            gaps.append(support_preflight_gap(str(preflight_path), f"support preflight repo_head {repo_head or 'missing'} does not match --build-sha {expected_sha}"))
    required_readiness = [
        "safe_to_run_personas",
        "same_sha_ready",
        "provider_auth_ready",
        "player_agent_auth_ready",
        "required_tools_ready",
        "persona_briefs_ready",
        "private_art_ready",
        "artifact_return_ready",
        "host_capacity_ready",
        "mac_handoff_required",
    ]
    for field in required_readiness:
        if readiness.get(field) is not True:
            gaps.append(support_preflight_gap(str(preflight_path), f"support preflight readiness.{field} was not true"))
    if readiness.get("blocking_categories"):
        gaps.append(
            support_preflight_gap(
                str(preflight_path),
                "support preflight blocking_categories is not empty: " + ", ".join(str(v) for v in readiness.get("blocking_categories")),
            )
        )
    if rri_plan.get("support_preflight_required_for_split_rollup") is not True:
        gaps.append(
            support_preflight_gap(
                str(preflight_path),
                "support preflight rri_plan.support_preflight_required_for_split_rollup was not true",
            )
        )
    rollup_template = str(rri_plan.get("rri_rollup_command_template") or "")
    if "--support-preflight-json" not in rollup_template:
        gaps.append(
            support_preflight_gap(
                str(preflight_path),
                "support preflight rri_plan.rri_rollup_command_template did not include --support-preflight-json",
            )
        )
    proof["valid"] = not gaps
    return proof, gaps


def validate_handoff_json(handoff_json: str, expected_sha: str) -> tuple[dict, list[dict]]:
    proof = {
        "path": handoff_json or "",
        "valid": False,
        "status": "",
        "handoff_score": 0,
        "commit_sha": "",
        "gates": {},
        # Image evidence is a SEPARATE, stricter signal than handoff validity: the
        # handoff stays valid for native_gate even when its app-status snapshots never
        # recorded health.image_probe_ok, but image_render may only ride the handoff
        # when EVERY required gate proved the probe + the private art root. The probe
        # is honest-but-weaker than a render rate: it proves the built app resolved a
        # representative descriptor for the live scene's imageScope, not a percentage
        # of all image requests.
        "image_evidence": {"image_probe_ok": False, "art_root_present": False, "gates": {}},
    }
    if not handoff_json:
        return proof, []

    handoff_path = Path(handoff_json)
    payload, error = read_json_with_error(handoff_path)
    if error:
        return proof, [handoff_gap(handoff_json, f"handoff JSON {error}")]

    gaps: list[dict] = []
    proof.update({
        "status": payload.get("status") or "",
        "handoff_score": payload.get("handoff_score") or 0,
        "commit_sha": payload.get("commit_sha") or "",
        "release_verdict": bool(payload.get("release_verdict")),
    })
    if payload.get("schema") != "worldos.app-handoff.v1":
        gaps.append(handoff_gap(str(handoff_path), "handoff schema is missing or wrong"))
    if payload.get("status") != "passed":
        gaps.append(handoff_gap(str(handoff_path), f"handoff status is {payload.get('status') or 'missing'}"))
    if payload.get("handoff_score") != 100:
        gaps.append(handoff_gap(str(handoff_path), f"handoff_score is {payload.get('handoff_score')!r}, expected 100"))
    if payload.get("dirty") is not False:
        gaps.append(handoff_gap(str(handoff_path), "handoff evidence was recorded from a dirty worktree"))
    if expected_sha and not build_sha_matches(str(payload.get("commit_sha") or ""), expected_sha):
        gaps.append(handoff_gap(str(handoff_path), f"handoff commit_sha {payload.get('commit_sha') or 'missing'} does not match --build-sha {expected_sha}"))

    gates = payload.get("gates")
    if not isinstance(gates, list):
        gates = []
        gaps.append(handoff_gap(str(handoff_path), "handoff gates list is missing"))
    by_name = {str(g.get("name") or ""): g for g in gates if isinstance(g, dict)}
    proof["gates"] = {}
    manifest_paths_seen: set[Path] = set()
    gate_image_probe: dict[str, bool] = {}
    gate_art_root: dict[str, bool] = {}
    for gate_name in REQUIRED_HANDOFF_GATES:
        gate = by_name.get(gate_name)
        if not gate:
            gaps.append(handoff_gap(str(handoff_path), f"handoff missing required gate {gate_name}"))
            continue
        manifest_value = str(gate.get("evidence_manifest") or "")
        manifest_path = resolve_manifest_path(handoff_path, manifest_value) if manifest_value else Path()
        if manifest_path in manifest_paths_seen:
            gaps.append(handoff_gap(gate_name, f"evidence_manifest reuses another gate's manifest: {manifest_path}"))
        manifest_paths_seen.add(manifest_path)
        gate_proof = {
            "status": gate.get("status") or "",
            "build_sha": gate.get("build_sha") or "",
            "evidence_manifest": str(manifest_path) if manifest_value else "",
        }
        proof["gates"][gate_name] = gate_proof
        if gate.get("status") != "passed":
            gaps.append(handoff_gap(gate_name, f"handoff gate status is {gate.get('status') or 'missing'}"))
        if gate.get("evidence_gaps"):
            gaps.append(handoff_gap(gate_name, "handoff gate reported evidence_gaps"))
        if expected_sha and not build_sha_matches(str(gate.get("build_sha") or ""), expected_sha):
            gaps.append(handoff_gap(gate_name, f"gate build_sha {gate.get('build_sha') or 'missing'} does not match --build-sha {expected_sha}"))
        if not manifest_value:
            gaps.append(handoff_gap(gate_name, "evidence_manifest path is missing"))
            continue
        manifest, manifest_error = read_json_with_error(manifest_path)
        if manifest_error:
            gaps.append(handoff_gap(str(manifest_path), f"evidence manifest {manifest_error}"))
            continue
        gate_proof["manifest_verdict"] = manifest.get("verdict") or ""
        if manifest.get("schema") != "worldos.app-evidence.v1":
            gaps.append(handoff_gap(str(manifest_path), "manifest schema is missing or wrong"))
        if manifest.get("gate_kind") != gate_name:
            gaps.append(handoff_gap(str(manifest_path), f"manifest gate_kind {manifest.get('gate_kind') or 'missing'} does not match {gate_name}"))
        if manifest.get("verdict") != "passed":
            gaps.append(handoff_gap(str(manifest_path), f"manifest verdict is {manifest.get('verdict') or 'missing'}"))
        if manifest.get("dirty") is not False:
            gaps.append(handoff_gap(str(manifest_path), "manifest was recorded from a dirty worktree"))
        if manifest.get("evidence_gaps"):
            gaps.append(handoff_gap(str(manifest_path), "manifest evidence_gaps is not empty"))
        for field in (
            "provider_family",
            "dm_model",
            "player_agent",
            "player_model",
            "scorer_provider",
            "scorer_model",
        ):
            if not str(manifest.get(field) or "").strip():
                gaps.append(handoff_gap(str(manifest_path), f"manifest missing {field}"))
        manifest_failure = manifest.get("failure") if isinstance(manifest.get("failure"), dict) else {}
        has_failure_fields = (
            ("failure_bucket" in manifest and "failure_detail" in manifest)
            or ("failure_bucket" in manifest_failure and "failure_detail" in manifest_failure)
        )
        if not has_failure_fields:
            gaps.append(handoff_gap(str(manifest_path), "manifest missing failure bucket/detail fields"))
        if expected_sha and not build_sha_matches(str(manifest.get("app_build_sha") or ""), expected_sha):
            gaps.append(handoff_gap(str(manifest_path), f"manifest app_build_sha {manifest.get('app_build_sha') or 'missing'} does not match --build-sha {expected_sha}"))

        handoff_gate = manifest.get("handoff_gate") if isinstance(manifest.get("handoff_gate"), dict) else {}
        art = manifest.get("art") if isinstance(manifest.get("art"), dict) else {}
        gate_art_root[gate_name] = art.get("private_root_present") is True
        live = manifest.get("live") if isinstance(manifest.get("live"), dict) else {}
        evidence_files = manifest.get("evidence_files") if isinstance(manifest.get("evidence_files"), dict) else {}
        checks = {
            "handoff_gate.ok": handoff_gate.get("ok") is True,
            "handoff_gate.app_status_ok": handoff_gate.get("app_status_ok") is True,
            "handoff_gate.session_surface_ok": handoff_gate.get("session_surface_ok") is True,
            "handoff_gate.move_sink_present": handoff_gate.get("move_sink_present") is True,
            "handoff_gate.private_art_present": handoff_gate.get("private_art_present") is True,
            "handoff_gate.can_act": handoff_gate.get("can_act") is True,
            "art.private_root_present": art.get("private_root_present") is True,
            "live.can_act": live.get("can_act") is True,
        }
        for check, ok in checks.items():
            if not ok:
                gaps.append(handoff_gap(str(manifest_path), f"{check} was not true"))
        if int(handoff_gate.get("enabled_action_count") or 0) <= 0 or int(live.get("enabled_action_count") or 0) <= 0:
            gaps.append(handoff_gap(str(manifest_path), "manifest did not prove enabled player actions"))
        if int(handoff_gate.get("evidence_gap_count") or 0) != 0:
            gaps.append(handoff_gap(str(manifest_path), "handoff_gate evidence_gap_count was not zero"))
        for evidence_kind in REQUIRED_HANDOFF_EVIDENCE_KINDS:
            values = evidence_files.get(evidence_kind)
            if not isinstance(values, list) or not values:
                gaps.append(handoff_gap(str(manifest_path), f"manifest missing evidence_files.{evidence_kind}"))
                continue
            for value in values:
                evidence_file = resolve_evidence_file(manifest_path, str(value))
                if not evidence_file.exists():
                    gaps.append(handoff_gap(str(manifest_path), f"manifest evidence_files.{evidence_kind} entry missing on disk: {value}"))
                    continue
                if evidence_kind == "app_status_snapshots":
                    app_status, app_status_error = read_json_with_error(evidence_file)
                    if app_status_error:
                        gaps.append(handoff_gap(str(evidence_file), f"app-status snapshot {app_status_error}"))
                        continue
                    health = app_status.get("health") if isinstance(app_status.get("health"), dict) else {}
                    gate_image_probe[gate_name] = (
                        gate_image_probe.get(gate_name, True) and health.get("image_probe_ok") is True
                    )
                    if app_status.get("schema") != "worldos.app-status.v1":
                        gaps.append(handoff_gap(str(evidence_file), "app-status schema is missing or wrong"))
                    if app_status.get("state_authority") != "engine":
                        gaps.append(handoff_gap(str(evidence_file), f"app-status state_authority {app_status.get('state_authority') or 'missing'} does not prove engine authority"))
                    if app_status.get("write_lane") != "/move":
                        gaps.append(handoff_gap(str(evidence_file), f"app-status write_lane {app_status.get('write_lane') or 'missing'} does not prove /move intent writes"))
                    app_status_build = app_status.get("build") if isinstance(app_status.get("build"), dict) else {}
                    if expected_sha and not build_sha_matches(str(app_status_build.get("sha") or ""), expected_sha):
                        gaps.append(handoff_gap(str(evidence_file), f"app-status build.sha {app_status_build.get('sha') or 'missing'} does not match --build-sha {expected_sha}"))
    proof["image_evidence"] = {
        # True only when EVERY required gate parsed >=1 app-status snapshot and ALL of
        # that gate's snapshots reported health.image_probe_ok:true. A gate with no
        # parsed snapshots stays absent from gate_image_probe and fails the all().
        "image_probe_ok": all(gate_image_probe.get(g) is True for g in REQUIRED_HANDOFF_GATES),
        "art_root_present": all(gate_art_root.get(g) is True for g in REQUIRED_HANDOFF_GATES),
        "gates": {
            g: {
                "image_probe_ok": gate_image_probe.get(g, False) is True,
                "art_root_present": gate_art_root.get(g, False) is True,
            }
            for g in REQUIRED_HANDOFF_GATES
        },
    }
    proof["valid"] = not gaps
    return proof, gaps


def infer_persona(run_dir: Path) -> str:
    name = run_dir.name
    for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer"):
        if name.endswith(f"-{persona}") or f"-{persona}-" in name:
            return persona
    return name


# A persona whose DM beats 429'd on the account session limit is INFRA-aborted, not a
# product failure (the rc3 lesson — a 429-storm rolled up a misleading 1.8). Detect it so
# the rollup attributes "quota" vs "broken build" correctly and never reads as a clean score.
_QUOTA_RE = re.compile(r"session limit|HTTP 429|hit your (?:session|usage) limit", re.I)
_RESET_RE = re.compile(r"resets [0-9: ]*[ap]m \(?(?:UTC|[A-Za-z/_]+)\)?", re.I)


def infra_abort_hint(run_dir: Path) -> str:
    """Return a non-empty reset hint (or the literal '429') if this run's backend 429'd, else ''."""
    bl = run_dir / "backend.log"
    try:
        text = bl.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not _QUOTA_RE.search(text):
        return ""
    m = _RESET_RE.search(text)
    return m.group(0) if m else "429"


def build_sha_evidence_gaps(persona_scores: list[dict], build_sha: str,
                            release_personas: list[str]) -> list[dict]:
    """native_gate's build-SHA contract, SCOPED to the canonical release personas.

    The release verdict is about the canonical five (REQUIRED_RELEASE_PERSONAS) + the Mac handoff,
    all at ONE SHA. Extra DIAGNOSTIC personas (opus-high / lean variants outside the release set)
    may run at other SHAs without invalidating the release — so they must NOT trip the "single
    build_sha" / "same-build" gates. (RRI 2026-06-09: 3 narrative variants stamped stale SHAs while
    newbie/veteran/adversarial/narrative/optimizer were all at the candidate SHA, falsely failing
    native_gate even though the Mac handoff + the 5 release personas were same-build.)
    """
    release = [p for p in persona_scores if str(p.get("persona") or "") in set(release_personas)]
    build_shas = sorted({str(p["run_build_sha"]) for p in release if p.get("run_build_sha")})
    missing = [p for p in release if not p.get("run_build_sha")]
    gaps: list[dict] = []
    if not build_sha:
        gaps.append({"gate": "native_gate", "missing": "--build-sha",
                     "detail": "release verdict requires the measured build SHA"})
    if missing:
        gaps.append({"gate": "native_gate", "missing": "per-run build_sha",
                     "detail": "missing run build_sha for: "
                     + ", ".join(str(p.get("persona") or p.get("run")) for p in missing)})
    if build_sha:
        mismatched = [p for p in release
                      if p.get("run_build_sha") and not build_sha_matches(str(p.get("run_build_sha")), build_sha)]
        if mismatched:
            gaps.append({"gate": "native_gate", "missing": "same-build persona evidence",
                         "detail": "run build_sha mismatch: "
                         + ", ".join(f"{p['persona']}={p['run_build_sha']}" for p in mismatched)})
    if len(build_shas) > 1:
        gaps.append({"gate": "native_gate", "missing": "single build_sha",
                     "detail": "mixed release-persona build_sha values: " + ", ".join(build_shas)})
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, help="comma-separated persona run dirs")
    ap.add_argument("--story", default="")
    ap.add_argument("--mech", default="")
    ap.add_argument("--behavioral", default="", choices=["", "GREEN", "RED"])
    ap.add_argument("--ui-audit", dest="ui_audit", default="", choices=["", "PASS", "FAIL"])
    ap.add_argument("--palette-live", dest="palette_live", default="", choices=["", "true", "false"])
    ap.add_argument("--expected-personas", default="", help="comma-separated persona names expected in this sweep")
    ap.add_argument("--behavioral-path", default="", help="path to the behavioral evidence source")
    ap.add_argument("--ui-audit-log", default="", help="path to the UI audit evidence source")
    ap.add_argument("--palette-source", default="", help="path or label for the palette-live evidence source")
    ap.add_argument("--handoff-json", default="", help="Mac app handoff gate JSON proving built-app smoke/play evidence")
    ap.add_argument("--support-preflight-json", default="", help="Support VM preflight JSON proving same-SHA heavy-lane readiness")
    ap.add_argument("--build-sha", dest="build_sha", default="")
    ap.add_argument("--abort-marker", default="", help="path to a sweep QUOTA_ABORT marker; if it "
                    "exists the rollup is forced to an ABORTED status (infra abort, not a product RRI)")
    ap.add_argument("--out", default="qa/RRI.json")
    ap.add_argument("--scorecard-row", action="store_true")
    ap.add_argument("--deterministic-only", dest="deterministic_only", action="store_true",
                    help="evaluate ONLY the gates that need no live LLM/persona evidence "
                    "(native_gate, ui_audit, image_render, palette_live, + latency when present) "
                    "and mark the LLM/persona gates SKIPPED (not FAILED) — an early advisory "
                    "'do the deterministic release gates hold?' signal, never the release verdict")
    args = ap.parse_args()

    run_dirs = [Path(p) for p in split_csv(args.runs)]
    expected_personas = split_csv(args.expected_personas)
    handoff_proof, handoff_evidence_gaps = validate_handoff_json(args.handoff_json, args.build_sha)
    support_preflight_proof, support_preflight_gaps = validate_support_preflight_json(args.support_preflight_json, args.build_sha)
    persona_scores = []
    harness_failures = []
    completed_personas: list[str] = []
    for idx, rd in enumerate(run_dirs):
        expected_for_run = expected_personas[idx] if idx < len(expected_personas) else ""
        rj = read_json(rd / "run.json")
        sc, score_error = read_json_with_error(rd / "score.json")
        if score_error:
            part_a_obj = rj.get("part_a") or {}
            part_b_obj = rj.get("part_b") or {}
            part_b = part_b_obj.get("persona_loop")
            harness_failures.append({
                "run": rd.name,
                "persona": expected_for_run or infer_persona(rd),
                "missing": "score.json" if score_error == "missing" else "score.json invalid",
                "detail": score_error,
                "part_a": part_a_obj.get("result") or "n/a",
                "part_b": part_b or "n/a",
                "part_a_failure_bucket": part_a_obj.get("failure_bucket") or "",
                "part_a_failure_detail": part_a_obj.get("failure_detail") or "",
                "part_b_failure_bucket": part_b_obj.get("failure_bucket") or "",
                "part_b_failure_detail": part_b_obj.get("failure_detail") or "",
            })
            continue
        schema_errors = score_schema_errors(sc)
        if schema_errors:
            part_a_obj = rj.get("part_a") or {}
            part_b_obj = rj.get("part_b") or {}
            harness_failures.append({
                "run": rd.name,
                "persona": expected_for_run or sc.get("persona") or infer_persona(rd),
                "missing": "score.json required fields",
                "detail": "; ".join(schema_errors),
                "required_fields": REQUIRED_SCORE_FIELDS,
                "part_a": part_a_obj.get("result") or "n/a",
                "part_b": part_b_obj.get("persona_loop") or "n/a",
                "part_a_failure_bucket": part_a_obj.get("failure_bucket") or "",
                "part_a_failure_detail": part_a_obj.get("failure_detail") or "",
                "part_b_failure_bucket": part_b_obj.get("failure_bucket") or "",
                "part_b_failure_detail": part_b_obj.get("failure_detail") or "",
            })
            continue
        rate, ok, total, image_source, image_gap = image_render_rate(rd, sc)
        lat_s_per_beat, lat_coldopen, lat_source = read_latency(rd, rj, sc)
        persona = sc.get("persona") or expected_for_run or infer_persona(rd)
        if persona:
            completed_personas.append(str(persona))
        unexpected_404s = sc.get("image_404s_unexpected")
        if isinstance(unexpected_404s, bool) or not isinstance(unexpected_404s, int):
            unexpected_404s = None
        persona_scores.append({
            "run": sc.get("run") or rd.name,
            "persona": persona,
            "completed_intro_flow": bool(sc.get("completed_intro_flow")),
            "satisfaction": sc.get("persona_satisfaction"),
            "gave_up": bool(sc.get("gave_up")),
            "critical": int(sc.get("bug_reports_critical", 0) or 0),
            "console_errors": int(sc.get("console_errors", 0) or 0),
            "image_rate": rate,
            "image_ok": ok,
            "image_total": total,
            "image_source": image_source,
            # Additive (audit F11-1a): a non-empty image_evidence_gap means this run
            # recorded image 404s with NO success denominator — an evidence gap, never
            # a fabricated rate. image_404s/unexpected are carried for honest detail.
            "image_evidence_gap": image_gap,
            "image_404s": int(sc.get("image_404s", 0) or 0),
            "image_404s_unexpected": unexpected_404s,
            # ADDITIVE latency evidence (Phase-3): None when this run carries no derivable
            # latency (a documented evidence-gap/skip for the latency gates, never a fail).
            "s_per_beat": lat_s_per_beat,
            "coldopen_s": lat_coldopen,
            "latency_source": lat_source,
            "run_build_sha": rj.get("build_sha") or "",
            "part_b_result": (rj.get("part_b") or {}).get("persona_loop") or "n/a",
            "part_b_score_pass": bool((rj.get("part_b") or {}).get("score_pass")),
            # FIX 1 (#623 false-cap): a NON-quota player_rc!=0 CRASH is INCONCLUSIVE evidence
            # (re-measure), not a product-quality fail. ui_playtest_app.sh stamps this true on
            # such a crash. We EXCLUDE these personas from score_pass_failed_personas / the
            # cross_persona_sat quality gate and instead surface them as an evidence gap (RED-cap
            # as harness_contaminated). The discriminator is the PROCESS exit, never the score —
            # a played low-score run exits rc=0, harness_error=False, and stays a clean quality RED.
            "part_b_harness_error": bool((rj.get("part_b") or {}).get("harness_error")),
            "part_a_result": (rj.get("part_a") or {}).get("result") or "n/a",
            "part_a_failure_bucket": (rj.get("part_a") or {}).get("failure_bucket") or "",
            "part_a_failure_detail": (rj.get("part_a") or {}).get("failure_detail") or "",
            "part_b_failure_bucket": (rj.get("part_b") or {}).get("failure_bucket") or "",
            "part_b_failure_detail": (rj.get("part_b") or {}).get("failure_detail") or "",
            # WS0 — the feature-engagement coverage block (inject_structural_coverage merges it
            # into score.json). None when absent (a legacy corpus) → the story_engagement gate is
            # an evidence-gap SKIP, never a fail (mirrors the latency-gate skip). Carried raw so
            # the per-system owed/engaged roll-up below reads it.
            "engagement_coverage": sc.get("engagement_coverage")
            if isinstance(sc.get("engagement_coverage"), dict) else None,
        })

    if not expected_personas:
        expected_personas = [p["persona"] for p in persona_scores if p.get("persona")]
        expected_personas.extend(h["persona"] for h in harness_failures if h.get("persona"))
    completed_set = set(completed_personas)
    missing_personas = [p for p in expected_personas if p not in completed_set]
    expected_complete = not missing_personas

    sats = [p["satisfaction"] for p in persona_scores if isinstance(p["satisfaction"], (int, float))]
    avg_sat = sum(sats) / len(sats) if sats else 0.0
    any_gave_up = any(p["gave_up"] for p in persona_scores)
    any_completed = any(p["completed_intro_flow"] for p in persona_scores)
    # FIX 1 (#623 false-cap): a persona whose player process CRASHED (part_b_harness_error) is
    # INCONCLUSIVE, not a quality fail — exclude it from the score_pass quality gate and route it
    # through the evidence_gaps/harness_contaminated machinery below (RED-cap as a re-measure).
    # A played low-score run has harness_error=False and STILL fails the gate (a clean quality RED).
    harness_error_personas = [p for p in persona_scores if p.get("part_b_harness_error")]
    score_pass_failed_personas = [str(p["persona"]) for p in persona_scores
                                  if not p.get("part_b_score_pass") and not p.get("part_b_harness_error")]
    score_pass_complete = bool(persona_scores) and not score_pass_failed_personas
    total_critical = sum(p["critical"] for p in persona_scores)
    total_console_errors = sum(p["console_errors"] for p in persona_scores)
    # weighted image rate across personas that recorded image traffic. A release
    # verdict needs a denominator for every scored persona; zero image requests is
    # an evidence gap, not a 100% pass.
    img_runs = [p for p in persona_scores if p["image_total"] > 0]
    image_missing_personas = [str(p["persona"]) for p in persona_scores if p["image_total"] <= 0]
    image_evidence_complete = bool(persona_scores) and not image_missing_personas
    img_rate = (sum(p["image_ok"] for p in img_runs) / sum(p["image_total"] for p in img_runs)) if img_runs else 0.0
    total_image_denominator = sum(p["image_total"] for p in img_runs)

    # ADDITIVE latency gates (Phase-3): the WORST (max) per-beat figure across personas that
    # actually recorded latency, judged against qa/latency_baseline.json. The aggregate is the
    # max so a single slow persona cannot be hidden by faster ones (the worldos-latency-forensics
    # discipline: a slow beat that trips a persona is a release blocker). When NO persona recorded
    # latency, the aggregate is None -> the gate is a documented evidence-gap/skip, never a new fail.
    latency_budget = load_latency_baseline()
    s_per_beat_values = [p["s_per_beat"] for p in persona_scores if p.get("s_per_beat") is not None]
    coldopen_values = [p["coldopen_s"] for p in persona_scores if p.get("coldopen_s") is not None]
    agg_s_per_beat = max(s_per_beat_values) if s_per_beat_values else None
    agg_coldopen_s = max(coldopen_values) if coldopen_values else None

    # image_render source selection. The VM cannot serve gitignored _private art and runs
    # a null image provider, so every VM /image request 404s BY CONSTRUCTION — those are
    # DESIGNED no-art outcomes, not render failures (audit F11-1: a real sweep always
    # records /image 404s, so "the VM has no denominator" was empirically false; treating
    # those 404s as a denominator kept this gate permanently un-passable). The denominator
    # therefore counts only UNEXPECTED network rows (designed no-art/placeholder rows are
    # excluded via X-Image-Outcome) and 404-only score.json counts are evidence gaps, never
    # fabricated rates. The Mac handoff is then the authoritative image evidence (built-app
    # image_probe_ok + private art root across every required handoff gate, at the SAME
    # --build-sha). Precedence is honest: any UNEXPECTED recorded VM image traffic computes
    # the REAL rate (a handoff can never paper over recorded unexpected 404s), and when
    # NEITHER source exists the gate stays a hard fail with an evidence gap.
    handoff_image_evidence = handoff_proof.get("image_evidence") if isinstance(handoff_proof.get("image_evidence"), dict) else {}
    handoff_image_ok = bool(
        args.handoff_json
        and args.build_sha
        and handoff_proof.get("valid")
        and handoff_image_evidence.get("image_probe_ok") is True
        and handoff_image_evidence.get("art_root_present") is True
    )
    if total_image_denominator > 0:
        image_render_source = "vm-network"
    elif persona_scores and handoff_image_ok:
        image_render_source = "mac-handoff"
    else:
        image_render_source = "none"

    story = read_json(Path(args.story)) if args.story else {}
    mech = read_json(Path(args.mech)) if args.mech else {}
    story_overall = float(story.get("overall", 0) or 0)
    mech_overall = float(mech.get("overall", 0) or 0)

    # native gate: read part_a from persona run.json, or accept an explicit
    # Mac-built app handoff proof when persona artifacts come from a VM/backend
    # sweep and therefore cannot prove dist/WorldOS.app directly.
    native = ""
    native_detail = ""
    native_source = ""
    part_a_failures = []
    for rd in run_dirs:
        rj = read_json(rd / "run.json")
        part_a_obj = rj.get("part_a") or {}
        pa = part_a_obj.get("result")
        if pa:
            bucket = part_a_obj.get("failure_bucket") or ""
            detail = part_a_obj.get("failure_detail") or ""
            if pa == "PASS" and not native:
                native = pa
                native_source = f"{rd}/run.json"
                native_detail = f"part_a={pa}"
            elif pa != "PASS":
                part_a_failures.append({
                    "run": rd.name,
                    "part_a": pa,
                    "failure_bucket": bucket,
                    "failure_detail": detail,
                })
    if not native and handoff_proof.get("valid"):
        native = "PASS"
        native_source = args.handoff_json
        native_detail = f"handoff_json={args.handoff_json} gates={','.join(REQUIRED_HANDOFF_GATES)}"
    split_vm_handoff_evidence = bool(persona_scores and native_source == args.handoff_json and args.handoff_json)

    # native_gate build-SHA contract — SCOPED to the canonical release personas (extra diagnostic
    # variants may run at other SHAs without invalidating the release verdict). See the helper.
    evidence_gaps = build_sha_evidence_gaps(persona_scores, args.build_sha, REQUIRED_RELEASE_PERSONAS)
    missing_release_personas = [p for p in REQUIRED_RELEASE_PERSONAS if p not in completed_set]
    if missing_release_personas:
        missing_detail = f"missing release persona(s): {', '.join(missing_release_personas)}"
        for gate in ("cross_persona_sat", "no_give_up", "zero_critical", "image_render"):
            evidence_gaps.append({
                "gate": gate,
                "missing": "canonical five-persona release set",
                "detail": missing_detail,
            })
    for h in harness_failures:
        buckets = ", ".join(
            value for value in (
                f"part_a_bucket={h.get('part_a_failure_bucket')}" if h.get("part_a_failure_bucket") else "",
                f"part_b_bucket={h.get('part_b_failure_bucket')}" if h.get("part_b_failure_bucket") else "",
            )
            if value
        )
        evidence_gaps.append({
            "gate": "cross_persona_sat",
            "missing": f"{h['run']}/{h.get('missing') or 'score.json'}",
            "detail": f"persona={h.get('persona') or 'unknown'} detail={h.get('detail') or ''} part_a={h.get('part_a') or 'n/a'} part_b={h.get('part_b') or 'n/a'} {buckets}".strip(),
        })
    # FIX 1 (#623 false-cap): a persona whose player process CRASHED (non-quota player_rc!=0) is
    # INCONCLUSIVE — surface it as a cross_persona_sat EVIDENCE GAP (re-measure), exactly like a
    # harness_failure, so it RED-caps as harness_contaminated rather than a quality FAIL. It is
    # already excluded from score_pass_failed_personas (the quality gate) above.
    for p in harness_error_personas:
        evidence_gaps.append({
            "gate": "cross_persona_sat",
            "missing": f"{p['run']}/run.json part_b.harness_error",
            "detail": f"persona={p.get('persona') or 'unknown'} part_b player process crashed (non-quota player_rc!=0) "
                      f"— INCONCLUSIVE, re-measure (not a quality fail) "
                      f"failure_bucket={p.get('part_b_failure_bucket') or ''} failure_detail={p.get('part_b_failure_detail') or ''}".strip(),
        })
    # A crashed (harness_error) persona's part_b is "FAIL", but it is INCONCLUSIVE, not a dropped
    # product arc — exclude it from the arc_completed product-failure attribution (it's already a
    # cross_persona_sat evidence gap above). Genuine non-PASS played runs still flow through here.
    failed_part_b = [p for p in persona_scores
                     if p.get("part_b_result") != "PASS" and not p.get("part_b_harness_error")]
    for p in failed_part_b:
        bucket = p.get("part_b_failure_bucket") or ""
        detail = p.get("part_b_failure_detail") or ""
        evidence_gaps.append({
            "gate": "arc_completed",
            "missing": f"{p['run']}/run.json part_b PASS",
            "detail": f"persona={p.get('persona') or 'unknown'} part_b={p.get('part_b_result')} score_pass={p.get('part_b_score_pass')} failure_bucket={bucket} failure_detail={detail}".strip(),
        })
    evidence_gaps.extend(handoff_evidence_gaps)
    if split_vm_handoff_evidence:
        if not args.support_preflight_json:
            evidence_gaps.append({
                "gate": "support_preflight",
                "missing": "--support-preflight-json",
                "detail": "VM/persona artifacts that rely on --handoff-json for Mac proof require a same-SHA support preflight artifact",
            })
        else:
            evidence_gaps.extend(support_preflight_gaps)
    for failure in part_a_failures:
        evidence_gaps.append({
            "gate": "native_gate",
            "missing": f"{failure['run']}/run.json part_a PASS",
            "detail": f"part_a={failure.get('part_a')} failure_bucket={failure.get('failure_bucket') or ''} failure_detail={failure.get('failure_detail') or ''}".strip(),
        })
    if not native:
        evidence_gaps.append({
            "gate": "native_gate",
            "missing": "run.json part_a.result or --handoff-json",
            "detail": "no persona run or Mac handoff bundle recorded native built-app transition evidence",
        })
    elif native != "PASS":
        evidence_gaps.append({
            "gate": "native_gate",
            "missing": "run.json part_a PASS",
            "detail": f"part_a={native} {native_detail}".strip(),
        })
    if not args.story:
        evidence_gaps.append({"gate": "story_craft", "missing": "--story", "detail": "story lens path not supplied"})
    elif "overall" not in story:
        evidence_gaps.append({"gate": "story_craft", "missing": args.story, "detail": "story lens JSON missing overall"})
    if not args.mech:
        evidence_gaps.append({"gate": "mechanical", "missing": "--mech", "detail": "mechanical lens path not supplied"})
    elif "overall" not in mech:
        evidence_gaps.append({"gate": "mechanical", "missing": args.mech, "detail": "mechanical lens JSON missing overall"})
    if not args.behavioral:
        evidence_gaps.append({"gate": "behavioral", "missing": "--behavioral", "detail": "behavioral result not supplied"})
    elif not args.behavioral_path:
        evidence_gaps.append({"gate": "behavioral", "missing": "--behavioral-path", "detail": "behavioral evidence path not supplied"})
    elif not Path(args.behavioral_path).exists():
        evidence_gaps.append({"gate": "behavioral", "missing": args.behavioral_path, "detail": "behavioral evidence path missing"})
    if not args.ui_audit:
        evidence_gaps.append({"gate": "ui_audit", "missing": "--ui-audit", "detail": "UI audit result not supplied"})
    elif not args.ui_audit_log:
        evidence_gaps.append({"gate": "ui_audit", "missing": "--ui-audit-log", "detail": "UI audit log path not supplied"})
    elif not Path(args.ui_audit_log).exists():
        evidence_gaps.append({"gate": "ui_audit", "missing": args.ui_audit_log, "detail": "UI audit log path missing"})
    # Personas whose image evidence is a 404 count with NO denominator (score.json
    # fallback, audit F11-1a). KNOWN unexpected 404s are a hard gap no handoff may
    # paper over; an unknown split (legacy capture) is a gap only when the Mac handoff
    # does not carry the gate — on the VM lane those 404s are designed no-art outcomes.
    image_gap_personas = [p for p in persona_scores if p.get("image_evidence_gap")]
    blocking_image_gap_personas = [
        p for p in image_gap_personas
        if isinstance(p.get("image_404s_unexpected"), int) and p["image_404s_unexpected"] > 0
    ]
    nonblocking_image_gap_personas = [
        p for p in image_gap_personas if p not in blocking_image_gap_personas
    ]
    if blocking_image_gap_personas:
        evidence_gaps.append({
            "gate": "image_render",
            "missing": "image 404 denominator",
            "detail": "unexpected image 404s recorded but no denominator: "
            + ", ".join(f"{p['persona']} ({p['image_evidence_gap']})" for p in blocking_image_gap_personas),
        })
    if nonblocking_image_gap_personas and image_render_source != "mac-handoff":
        evidence_gaps.append({
            "gate": "image_render",
            "missing": "image 404 denominator",
            "detail": "image 404s recorded but no denominator (404-only score.json fallback; "
            "designed no-art 404s are indistinguishable without X-Image-Outcome): "
            + ", ".join(f"{p['persona']}={p['image_404s']}" for p in nonblocking_image_gap_personas),
        })
    if image_missing_personas and image_render_source != "mac-handoff":
        image_gap_detail = f"no /image requests recorded for: {', '.join(image_missing_personas)}"
        if args.handoff_json and image_render_source == "none":
            image_gap_detail += (
                "; Mac handoff supplied but did not prove image evidence"
                " (needs valid same-SHA handoff with health.image_probe_ok:true"
                " + art root present across all required gates)"
            )
        evidence_gaps.append({
            "gate": "image_render",
            "missing": "network.ndjson image denominator",
            "detail": image_gap_detail,
        })
    if not args.palette_live:
        evidence_gaps.append({"gate": "palette_live", "missing": "--palette-live", "detail": "palette-live result not supplied"})
    elif not args.palette_source:
        evidence_gaps.append({"gate": "palette_live", "missing": "--palette-source", "detail": "palette-live evidence source not supplied"})
    elif looks_like_path(args.palette_source) and not Path(args.palette_source).exists():
        evidence_gaps.append({"gate": "palette_live", "missing": args.palette_source, "detail": "palette-live evidence source missing"})
    evidence_gap_gates = {gap["gate"] for gap in evidence_gaps}
    native_evidence_gap_gates = {"native_gate"}
    if split_vm_handoff_evidence:
        native_evidence_gap_gates.add("support_preflight")
    native_gate_detail = f"source={native_source or 'n/a'} {native_detail or 'part_a=' + (native or 'n/a')}".strip()
    if image_render_source == "mac-handoff":
        # The Mac handoff also rides the support-preflight contract on a split sweep:
        # image_render must not pass off a handoff whose split rollup is unproven.
        image_render_ok = not (evidence_gap_gates & ({"image_render"} | native_evidence_gap_gates))
        image_render_detail = (
            "source=mac-handoff; built-app image_probe_ok + private art root proven across "
            f"{','.join(REQUIRED_HANDOFF_GATES)} at --build-sha {args.build_sha} "
            "(representative built-app probe, NOT a VM render rate; vm denominator=0)"
        )
    else:
        image_render_ok = image_evidence_complete and img_rate >= 0.95 and "image_render" not in evidence_gap_gates
        image_render_detail = f"source={image_render_source}; rate={img_rate:.2%}; denominator={total_image_denominator}"

    # ---- ADDITIVE latency gates (Phase-3) ----
    # s_per_beat / coldopen are HARD gates ONLY when latency evidence is present AND over
    # budget; ABSENT latency -> the gate is SKIPPED with a documented evidence gap, never a
    # new false fail. A skipped gate is excluded from passed/total so an evidence-less run's
    # RRI + release_ready are byte-identical to today (every pre-existing result is unchanged).
    s_per_beat_budget = latency_budget["s_per_beat_budget"]
    coldopen_s_budget = latency_budget["coldopen_s_budget"]
    latency_s_per_beat_ok = agg_s_per_beat is None or agg_s_per_beat <= s_per_beat_budget
    latency_coldopen_ok = agg_coldopen_s is None or agg_coldopen_s <= coldopen_s_budget
    latency_s_per_beat_detail = (
        f"s_per_beat={agg_s_per_beat if agg_s_per_beat is not None else 'n/a (evidence gap)'}; "
        f"budget={s_per_beat_budget}"
    )
    latency_coldopen_detail = (
        f"coldopen_s={agg_coldopen_s if agg_coldopen_s is not None else 'n/a (evidence gap)'}; "
        f"budget={coldopen_s_budget}"
    )

    # ---- WS0 ADDITIVE story_engagement gate (the dead-system tracker) ----
    # Roll the per-persona feature-engagement blocks (qa/feature_engagement.engagement_coverage,
    # merged into score.json by inject_structural_coverage) up across the sweep. A system is
    # "owed" if ANY persona owed it (engaged OR inert), "engaged" if ANY persona engaged it;
    # a system is INERT for the sweep iff it was owed by at least one persona AND no persona ever
    # engaged it — the authored subsystem was dead across the WHOLE sweep. The gate FAILS only on
    # a FATAL inert system; with WS0 all-WARN, a WARN-only inert set never fails the gate (it is
    # reported, not gated) — strictly additive. EVIDENCE-GAP SKIP: if NO persona block carries
    # engagement_coverage (a legacy corpus / a run before this stamping), the gate is SKIPPED, not
    # failed, so RRI math stays byte-identical (mirrors the latency-gate skip).
    engagement_blocks = [p["engagement_coverage"] for p in persona_scores
                         if isinstance(p.get("engagement_coverage"), dict)]
    engagement_evidence_present = bool(engagement_blocks)
    sweep_engaged: set[str] = set()
    sweep_owed: dict[str, str] = {}  # system id -> worst-seen severity ('fatal' beats 'warn')
    for blk in engagement_blocks:
        for sid in blk.get("engaged", []) or []:
            sweep_engaged.add(str(sid))
            sweep_owed.setdefault(str(sid), "warn")
        for item in blk.get("inert", []) or []:
            sid = str(item.get("id", ""))
            if not sid:
                continue
            sev = "fatal" if item.get("severity") == "fatal" else "warn"
            if sweep_owed.get(sid) != "fatal":
                sweep_owed[sid] = sev
    sweep_inert = sorted(sid for sid in sweep_owed if sid not in sweep_engaged)
    sweep_inert_fatal = sorted(sid for sid in sweep_inert if sweep_owed.get(sid) == "fatal")
    sweep_inert_warn = sorted(sid for sid in sweep_inert if sweep_owed.get(sid) != "fatal")
    # PASS unless a FATAL system is inert across the whole sweep. (All-WARN ⇒ always passes when
    # evidence is present; a WARN-only inert set is surfaced, not gated.)
    story_engagement_ok = not sweep_inert_fatal
    story_engagement_detail = (
        f"inert_fatal={sweep_inert_fatal or 'none'}; inert_warn={sweep_inert_warn or 'none'}; "
        f"engaged={sorted(sweep_engaged) or 'none'}"
        if engagement_evidence_present
        else "n/a (no engagement_coverage in any persona score.json — evidence gap)"
    )

    # ---- the gate set (each evaluated gate contributes to RRI; all must hold for 10/10) ----
    gates = {
        "native_gate":        (native == "PASS" and not (evidence_gap_gates & native_evidence_gap_gates),
                               native_gate_detail),
        "arc_completed":      (any_completed and "arc_completed" not in evidence_gap_gates,
                               f"completed_intro_flow on >=1 persona"),
        "cross_persona_sat":  (not missing_release_personas and expected_complete and avg_sat >= 7.0 and score_pass_complete,
                               f"avg={avg_sat:.1f}/10 over {len(sats)}; score_pass_failed={score_pass_failed_personas or 'none'}; missing={missing_personas or 'none'}; release_missing={missing_release_personas or 'none'}"),
        "no_give_up":         (not any_gave_up and "no_give_up" not in evidence_gap_gates,
                               f"any_gave_up={any_gave_up}"),
        "zero_critical":      (total_critical == 0 and total_console_errors == 0 and "zero_critical" not in evidence_gap_gates,
                               f"critical={total_critical}; console_errors={total_console_errors}"),
        "story_craft":        (story_overall >= 4.3 and "story_craft" not in evidence_gap_gates,
                               f"story={story_overall or 'n/a'}"),
        "mechanical":         (mech_overall >= 4.5 and "mechanical" not in evidence_gap_gates,
                               f"mech={mech_overall or 'n/a'}"),
        "behavioral":         (args.behavioral == "GREEN" and "behavioral" not in evidence_gap_gates,
                               f"behavioral={args.behavioral or 'n/a'}"),
        "ui_audit":           (args.ui_audit == "PASS" and "ui_audit" not in evidence_gap_gates,
                               f"ui_audit={args.ui_audit or 'n/a'}"),
        "image_render":       (image_render_ok, image_render_detail),
        "palette_live":       (args.palette_live == "true" and "palette_live" not in evidence_gap_gates,
                               f"palette_live={args.palette_live or 'n/a'}"),
        "latency_s_per_beat": (latency_s_per_beat_ok, latency_s_per_beat_detail),
        "latency_coldopen":   (latency_coldopen_ok, latency_coldopen_detail),
        "story_engagement":   (story_engagement_ok, story_engagement_detail),
    }

    # SKIPPED gates are excluded from passed / total_gates (never counted as pass OR fail):
    #   * a latency gate with NO evidence -> evidence-gap skip (additive invariant).
    #   * EVERY LLM/persona gate in --deterministic-only -> SKIPPED, not FAILED (early signal).
    skipped_gates: list[str] = []
    if agg_s_per_beat is None:
        skipped_gates.append("latency_s_per_beat")
    if agg_coldopen_s is None:
        skipped_gates.append("latency_coldopen")
    # WS0: no engagement evidence anywhere → the story_engagement gate is an evidence-gap skip
    # (excluded from passed/total), so a legacy corpus's RRI is byte-identical (mirrors latency).
    if not engagement_evidence_present:
        skipped_gates.append("story_engagement")
    if args.deterministic_only:
        skipped_gates.extend(g for g in LLM_PERSONA_GATES if g not in skipped_gates)
    skipped_set = set(skipped_gates)

    evaluated = {name: ok for name, (ok, _) in gates.items() if name not in skipped_set}
    passed = sum(1 for ok in evaluated.values() if ok)
    total_gates = len(evaluated)

    # RRI: each EVALUATED gate worth 10/total; HARD FLOOR — a missed gate can't be hidden by
    # others. (Equal weight keeps it honest: "10/10" literally means every evaluated gate held.)
    rri = round(10.0 * passed / total_gates, 1) if total_gates else 0.0
    failed = [name for name, ok in evaluated.items() if not ok]
    # Deterministic-only is an early ADVISORY signal, never the release verdict: report the
    # deterministic subset's verdict separately so CI/the agent can read "do the deterministic
    # gates hold?" without conflating it with the full five-persona release decision.
    deterministic_gate_names = [
        name for name in (*DETERMINISTIC_GATES, *LATENCY_GATES)
        if name not in skipped_set
    ]
    deterministic_failed_gates = [name for name in deterministic_gate_names if not evaluated.get(name, True)]
    deterministic_pass = not deterministic_failed_gates
    if missing_release_personas and "missing_release_personas" not in failed:
        failed.insert(0, "missing_release_personas")
    if missing_personas and "missing_personas" not in failed:
        failed.insert(0, "missing_personas")
    # --deterministic-only NEVER claims a release verdict (the LLM gates are unproven, only
    # skipped). The full-mode release_ready logic is unchanged.
    release_ready = (
        not args.deterministic_only
        and passed == total_gates
        and not evidence_gaps
        and not missing_personas
        and not harness_failures
    )

    # Distinct build SHAs across ALL persona runs (diagnostic output field; the native_gate CONTRACT is
    # release-persona-scoped via build_sha_evidence_gaps). Restored in main()'s scope after #723 factored
    # the gate logic into the helper and left this output reference dangling -> NameError at rollup time.
    build_shas = sorted({str(p["run_build_sha"]) for p in persona_scores if p.get("run_build_sha")})

    # Infra-abort attribution (the rc3 lesson): a harness-failed persona whose DM beats 429'd on
    # the account session limit is a QUOTA abort, not a broken build. An explicit --abort-marker
    # (the sweep's QUOTA_ABORT file) also forces ABORTED. When infra-aborted, this rollup is NOT a
    # product RRI — the status/verdict say so loudly so it can never be recorded as a clean score.
    infra_aborted_personas = []
    for rd in run_dirs:
        hint = infra_abort_hint(rd)
        if hint:
            infra_aborted_personas.append(
                {"persona": infer_persona(rd), "run": rd.name, "reset_hint": hint})
    abort_marker_present = bool(args.abort_marker and Path(args.abort_marker).is_file())
    abort_detail = ""
    if abort_marker_present:
        try:
            abort_detail = Path(args.abort_marker).read_text(encoding="utf-8").strip()
        except OSError:
            abort_detail = "abort marker present"
    elif infra_aborted_personas:
        abort_detail = "; ".join(
            f"{p['persona']}: {p['reset_hint']}" for p in infra_aborted_personas)
    aborted = bool(infra_aborted_personas or abort_marker_present)
    if aborted:
        release_ready = False  # a quota-aborted sweep is never release-ready, regardless of gates

    result = {
        "rri": rri,
        "status": "ABORTED" if aborted else ("READY" if release_ready else "NOT_READY"),
        "aborted": aborted,
        "abort_reason": "quota_session_limit" if aborted else "",
        "abort_detail": abort_detail,
        "infra_aborted_personas": infra_aborted_personas,
        "release_ready": release_ready,
        "release_verdict_gate": RELEASE_VERDICT_GATE,
        "gate_split_contract": GATE_SPLIT_CONTRACT,
        "partial": bool(missing_personas or evidence_gaps),
        "harness_contaminated": bool(missing_personas or harness_failures or evidence_gaps),
        "expected_personas": expected_personas,
        "required_release_personas": REQUIRED_RELEASE_PERSONAS,
        "completed_personas": completed_personas,
        "missing_personas": missing_personas,
        "missing_release_personas": missing_release_personas,
        "harness_failures": harness_failures,
        "evidence_gaps": evidence_gaps,
        "handoff_evidence": {
            **handoff_proof,
            "evidence_gaps": handoff_evidence_gaps,
        },
        "support_preflight_evidence": {
            **support_preflight_proof,
            "evidence_gaps": support_preflight_gaps,
        },
        "gates_passed": passed,
        "gates_total": total_gates,
        "failed_gates": failed,
        # ADDITIVE Phase-3 signal fields. Empty/false in the default (full) mode with latency
        # evidence absent, so every pre-existing field above is unchanged.
        "deterministic_only": bool(args.deterministic_only),
        "deterministic_gates": deterministic_gate_names,
        "deterministic_failed_gates": deterministic_failed_gates,
        "deterministic_pass": deterministic_pass,
        "skipped_gates": skipped_gates,
        "build_sha": args.build_sha,
        "artifact_sources": {
            "behavioral": args.behavioral_path or "argument",
            "ui_audit": args.ui_audit_log or "argument",
            "palette_live": args.palette_source or "argument",
            "story": args.story or "",
            "mechanical": args.mech or "",
            "handoff_json": args.handoff_json or "",
            "support_preflight_json": args.support_preflight_json or "",
            "runs": [str(p) for p in run_dirs],
            "images": sorted({p["image_source"] for p in persona_scores if p.get("image_source")}),
        },
        "signals": {
            "native_gate": native,
            "native_gate_source": native_source,
            "handoff_proof": handoff_proof,
            "support_preflight": support_preflight_proof,
            "arc_completed": any_completed,
            "cross_persona_satisfaction": round(avg_sat, 1),
            "score_pass_failed_personas": score_pass_failed_personas,
            # FIX 1 (#623 false-cap): personas reclassified as INCONCLUSIVE (player-process crash,
            # non-quota) — excluded from score_pass_failed_personas, surfaced as evidence gaps.
            "harness_error_personas": [str(p["persona"]) for p in harness_error_personas],
            "any_gave_up": any_gave_up,
            "total_critical_bugs": total_critical,
            "total_console_errors": total_console_errors,
            "story_overall": story_overall,
            "mech_overall": mech_overall,
            "behavioral": args.behavioral,
            "ui_audit": args.ui_audit,
            "image_render_rate": round(img_rate, 4),
            "image_render_source": image_render_source,
            "image_request_denominator": total_image_denominator,
            "image_missing_personas": image_missing_personas,
            # Additive (audit F11-1a): personas whose runs recorded image 404s with no
            # success denominator — honest record even when the Mac handoff carries the gate.
            "image_404_personas_without_denominator": [
                str(p["persona"]) for p in image_gap_personas
            ],
            "palette_live": args.palette_live,
            "run_build_shas": build_shas,
            # ADDITIVE latency signals (Phase-3). None when no persona recorded latency
            # (the latency gates are an evidence-gap skip, never a fabricated 0.0).
            "latency_s_per_beat": agg_s_per_beat,
            "latency_coldopen_s": agg_coldopen_s,
            "latency_s_per_beat_budget": s_per_beat_budget,
            "latency_coldopen_budget": coldopen_s_budget,
            "latency_sources": sorted({
                str(p["latency_source"]) for p in persona_scores
                if p.get("latency_source") and p.get("latency_source") != "none"
            }),
            # WS0 engagement signals. None/empty when no persona carried an engagement block
            # (the story_engagement gate is then an evidence-gap skip, never a fabricated pass).
            "engagement_evidence_present": engagement_evidence_present,
            "engagement_engaged": sorted(sweep_engaged),
            "engagement_inert": sweep_inert,
            "engagement_inert_fatal": sweep_inert_fatal,
            "engagement_inert_warn": sweep_inert_warn,
        },
        "gate_detail": {name: detail for name, (ok, detail) in gates.items()},
        "personas": persona_scores,
    }

    out = Path(args.out)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # human line
    if aborted:
        print(f"QUOTA-ABORTED — claude account session limit (HTTP 429): {abort_detail}")
        print(f"  This is an INFRA abort, NOT a product RRI. The {rri}/10 below is NOT a measurement; "
              f"re-run after the quota resets.")
    if args.deterministic_only:
        print(f"DETERMINISTIC-ONLY RRI {rri}/10  ({passed}/{total_gates} deterministic gates)  "
              f"deterministic_pass={deterministic_pass}  (advisory — NOT the release verdict; "
              f"LLM/persona gates SKIPPED: {', '.join(g for g in skipped_gates if g not in LATENCY_GATES) or 'none'})")
    else:
        print(f"RRI {rri}/10  ({passed}/{total_gates} gates)  release_ready={release_ready}")
    if skipped_gates:
        print("  SKIPPED (not failed): " + ", ".join(skipped_gates))
    if failed:
        details = []
        for f in failed:
            if f == "missing_personas":
                details.append(f"{f} [{missing_personas}]")
            elif f == "missing_release_personas":
                details.append(f"{f} [{missing_release_personas}]")
            else:
                details.append(f"{f} [{gates[f][1]}]")
        print("  FAILED: " + ", ".join(details))
    if evidence_gaps:
        print("  EVIDENCE GAPS: " + "; ".join(f"{g['gate']} missing {g['missing']}" for g in evidence_gaps))

    # WS0 ENGAGEMENT section — name each dead authored system across the sweep + a fix hint, so an
    # all-inert subsystem (the failure WS0 exists to surface) is visible even while it is WARN-only.
    if engagement_evidence_present:
        if sweep_inert:
            print("  ENGAGEMENT — INERT systems across the sweep (authored but never engaged):")
            for sid in sweep_inert:
                sev = sweep_owed.get(sid, "warn").upper()
                hint = _ENGAGEMENT_FIX_HINTS.get(sid, "engage the system's engine tool in a real beat")
                print(f"    [{sev}] {sid} — {hint}")
            if not sweep_inert_fatal:
                print("    (all WARN — reported, not gated; the story_engagement gate still PASSES)")
        else:
            print("  ENGAGEMENT — every owed authored system was engaged "
                  f"({sorted(sweep_engaged)})")

    if args.scorecard_row:
        sha = (args.build_sha or "?")[:7]
        if aborted:
            verdict = "QUOTA-ABORTED"
        elif missing_personas or evidence_gaps or harness_failures:
            verdict = "PARTIAL/HARNESS"
        else:
            verdict = "**GREEN**" if release_ready else "RED"
        row = (f"| RRI-{sha} | (date) | baldurs-gate | {len(expected_personas) or len(persona_scores)}-persona | sonnet | gate | "
               f"{verdict} | {story_overall or '—'} | "
               f"{mech_overall or '—'} | — | **{rri}** | "
               f"RRI {passed}/{total_gates}; failed: {', '.join(failed) or 'none'} |")
        print(row)

    if args.deterministic_only:
        # Advisory exit: green when the deterministic subset holds (LLM gates are skipped,
        # never failed), red when a deterministic gate misses. Never claims release_ready.
        return 0 if (deterministic_pass and not aborted) else 1
    return 0 if release_ready else 1


if __name__ == "__main__":
    sys.exit(main())
