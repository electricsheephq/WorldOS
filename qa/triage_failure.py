#!/usr/bin/env python3
"""Turn a WorldOS app FAILURE BUCKET into an actionable triage (read-only reporter).

WHY THIS EXISTS
---------------
``qa/app_failure_buckets.py`` classifies a built-app run into one of ten STABLE buckets
(``no_app`` … ``permission_prompt``). That tells an agent *what* failed in a routable way,
but not *what to do next*. This module is the missing second step: given a bucket name (and,
optionally, the run dir it came from), it answers three questions the MAIN IMPLEMENTING AGENT
asks on every red run:

  1. LIKELY CAUSE(S) — the small set of things that produce this bucket.
  2. NEXT DIAGNOSTIC + RETRY ENV — the cheapest thing to look at / the env knobs to retry with.
  3. IS THIS INFRA/MEASUREMENT OR PRODUCT? — i.e. did the *lane/auth/measurement* fall over
     (re-run with a fixed lane; do NOT file a product bug), or is this a real DEFECT in the
     shipped app (engine/viewer code must change)?

The INFRA/PRODUCT split is the load-bearing call. Buckets like ``no_provider`` and
``permission_prompt`` are almost always a *lane gap* — a missing mint, an unauthorized macOS
prompt, a gateway/auth hole — and a product bug filed against them is noise (the precedent is
release_readiness.py's "INFRA abort, NOT a product RRI"). Buckets like ``no_narration`` and
``move_rejected`` describe the app being seated and player-ready but *misbehaving* — that is a
real defect. ``no_actor`` / ``no_actions`` straddle the line; we route them to PRODUCT (the app
seated a launcher but failed to seat the player), and call out the infra possibility in the cause
list rather than mislabel the class.

PURE READER CONTRACT
--------------------
This tool reads artifacts and prints a report. It writes NOTHING — no snapshots, no scores_db,
no ledger, no RRI.json. The ``--run`` enrichment opens ``app-status.*.json`` read-only and
surfaces the artifact's *own* recorded ``failure_detail`` as evidence; a missing/garbage run dir
degrades to "no evidence" instead of crashing. The engine remains the sole writer of state.

It REUSES ``APP_FAILURE_BUCKETS`` from ``app_failure_buckets.py`` (it does not re-declare the
tuple); an unknown bucket degrades gracefully to a generic report flagged ``known: false`` rather
than raising.

USAGE
-----
    # CLI — human report:
    python3 qa/triage_failure.py --bucket no_provider
    # CLI — JSON for an agent:
    python3 qa/triage_failure.py --bucket no_narration --json
    # CLI — enrich with a run dir's app-status evidence (read-only):
    python3 qa/triage_failure.py --bucket no_provider --run path/to/run --json

    # From Python:
    from triage_failure import triage
    rep = triage("no_provider", run_dir=Path("path/to/run"))
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Import the canonical bucket tuple. Prefer the qa-package path (matches the existing
# qa/test_app_failure_buckets.py import), and fall back to a sys.path-relative import so the
# module is usable from any cwd (the CI-proven pattern in qa/test_detect_regression.py).
try:  # pragma: no cover - exercised implicitly by both import environments
    from qa.app_failure_buckets import APP_FAILURE_BUCKETS
except Exception:  # pragma: no cover
    _QA_DIR = Path(__file__).resolve().parent
    if str(_QA_DIR) not in sys.path:
        sys.path.insert(0, str(_QA_DIR))
    from app_failure_buckets import APP_FAILURE_BUCKETS  # type: ignore[no-redef]


# The two stable triage classes. INFRA/MEASUREMENT == re-run on a fixed lane, do NOT file a
# product bug. PRODUCT == a real defect in the shipped app; engine/viewer code must change.
CLASS_INFRA = "INFRA/MEASUREMENT"
CLASS_PRODUCT = "PRODUCT"

# Routing of the canonical buckets. Kept as explicit frozensets so the test can assert every
# bucket is classified exactly once (no silent gap, no double-claim).
#
# INFRA/MEASUREMENT: the lane/auth/measurement fell over before the app could fairly be judged.
#   no_app           -> the build/launch itself failed (toolchain/build lane), not gameplay.
#   no_launcher      -> the viewer/app-status endpoint never answered on the same port (harness lane).
#   no_provider      -> no minted live provider with can_act:true (auth/lane gap — the classic one).
#   no_art           -> the private art root was absent (art-repo wiring / checkout lane).
#   permission_prompt-> a macOS permission/accessibility/screen-recording prompt blocked the run.
INFRA_BUCKETS = frozenset({"no_app", "no_launcher", "no_provider", "no_art", "permission_prompt"})

# PRODUCT: the app got far enough that the failure describes a real defect in the shipped product.
#   no_actor         -> app-status seated a launcher but never seated a player actor.
#   no_actions       -> the player was seated but had zero enabled actions (UI/engine wiring).
#   move_rejected    -> a player move was rejected / never reached /move (engine or viewer defect).
#   no_narration     -> player-ready but the DM produced no chat/narration (resolver/viewer defect).
#   console_error    -> a browser console/page error fired during play (viewer JS defect).
PRODUCT_BUCKETS = frozenset({"no_actor", "no_actions", "move_rejected", "no_narration", "console_error"})


# Per-bucket triage knowledge. Each entry: likely causes, the cheapest next diagnostic, and the
# retry env vars an agent would set to re-run the failing lane. Retry env is intentionally EMPTY
# for pure-product defects (re-running with a different env won't fix a code bug) and populated
# for infra/lane buckets where a knob actually changes the outcome.
_TRIAGE: dict[str, dict[str, Any]] = {
    "no_app": {
        "causes": [
            "WorldOS.app failed to build (Swift/toolchain error) or did not launch / crashed on start",
            "the dist app is stale or missing for the SHA under test",
            "the .app's PATH could not resolve the engine/launcher CLIs at startup (login-shell PATH gap)",
        ],
        "next_diagnostic": "read the build log / launch stderr; confirm dist/WorldOS.app exists for this SHA and `Shell.which` resolves the CLIs",
        "retry_env": {
            "WORLDOS_REPO_ROOT": "absolute repo root the app should resolve CLIs from",
            "WORLDOS_BEAT_TIMEOUT": "raise if the build/launch is slow rather than broken",
        },
    },
    "no_launcher": {
        "causes": [
            "the launcher viewer never answered /openworlds/ AND /app-status on the same localhost port",
            "app-status reported the launcher/viewer unhealthy (ok:false)",
            "a visible browser tab is a STALE rendered page with no same-port /app-status backing it",
        ],
        "next_diagnostic": "curl the same-port /app-status; never trust a screenshot alone (a cached tab can fool the harness)",
        "retry_env": {
            "WORLDOS_COLDOPEN_TIMEOUT": "raise if the launcher is slow to come up",
            "CLAWDND_LAUNCH_LOCK_WAIT": "raise if a launch lock is contended",
        },
    },
    "no_provider": {
        "causes": [
            "no minted live provider viewer ever reported can_act:true (the faithful backend never went live)",
            "a masked `claude -p` 401 / apiKeySource:none — a broken cold-open is usually a MASKED auth failure, not a race",
            "the selected provider lane (Claude/Codex) was not authorized or the CLI version was too old",
        ],
        "next_diagnostic": "check provider auth FIRST (claude login / Codex CLI >=0.120.0); grep the run trace for 401 / apiKeySource:none before assuming a race",
        "retry_env": {
            "CLAWDND_PROVIDER": "the provider lane to mint (claude|codex)",
            "CLAWDND_DM_MODEL": "DM model id for the lane under test",
            "ANTHROPIC_API_KEY": "set/refresh if the Claude lane is key-gated",
            "CLAWDND_DM_RETRY_SESSION": "retry-with-fresh-session knob if the first mint 401'd",
        },
    },
    "no_art": {
        "causes": [
            "the private art root was not present in app-status (art-repo not wired / not checked out)",
            "the art-repo root env points at the wrong path for this checkout",
        ],
        "next_diagnostic": "confirm the private art root exists and the art-repo-root env points at it; this is wiring, not (usually) missing assets",
        "retry_env": {
            "WORLDOS_ART_REPO_ROOT": "absolute path to the private art root",
            "CLAWDND_ART_REPO_ROOT": "alias for the art-repo root the engine reads",
        },
    },
    "no_actor": {
        "causes": [
            "app-status / session-surface seated a launcher but never reported an active player actor",
            "the faithful backend went live (can_act:true) yet never seated a player character",
            "(infra possibility) the provider mint flapped after can_act so no PC was ever seated — verify the trace before filing",
        ],
        "next_diagnostic": "read app-status.final.json `live.actor` and session-surface `actionModel.actor`; if can_act:true but no actor, this is a seating defect",
        "retry_env": {
            "WORLDOS_ACTOR_MODEL": "actor model id (set only to rule out an actor-lane infra cause)",
            "WORLDOS_ACTOR_TIMEOUT": "raise only to rule out a slow-seat infra cause",
        },
    },
    "no_actions": {
        "causes": [
            "app-status reported zero enabled player actions (the player is seated but cannot act)",
            "the action model / UI never wired the enabled-action list to the player",
            "the player reached the table but the action surface stayed empty",
        ],
        "next_diagnostic": "read app-status `live.enabled_action_count`; a seated PC with zero enabled actions is a UI/engine action-wiring defect",
        "retry_env": {},
    },
    "move_rejected": {
        "causes": [
            "a player move was rejected or returned 4xx/5xx and never reached /move",
            "the move payload failed engine validation (a real engine defect or a stale viewer contract)",
            "the viewer never sent the move (button wired to nothing)",
        ],
        "next_diagnostic": "read moves.ndjson / network.ndjson for the /move request+status; a rejected/absent move is an engine-or-viewer defect, not a lane gap",
        "retry_env": {},
    },
    "no_narration": {
        "causes": [
            "app-status reported zero chat/narration lines — the player is ready but the DM said nothing",
            "the DM turn ended on a tool call / 3rd-person status with EMPTY reply text and the narration fallback did not recover prose",
            "the resolver loop or viewer dropped the player-facing 2nd-person scene",
        ],
        "next_diagnostic": "read the session log for narration|dialogue kinds and check dm_narration_fallback.py recovery; player-ready-but-silent is a resolver/viewer defect",
        "retry_env": {},
    },
    "console_error": {
        "causes": [
            "a browser console error / pageerror / uncaught exception fired during the app playtest",
            "a viewer JS defect (a broken bundle, a null deref, a failed fetch) surfaced at runtime",
        ],
        "next_diagnostic": "read console.ndjson for the first error and its stack; a console error during play is a viewer JS defect",
        "retry_env": {},
    },
    "permission_prompt": {
        "causes": [
            "a macOS permission prompt blocked the run (accessibility / screen-recording / AXIsProcessTrusted)",
            "the run lane was not pre-granted the OS permissions the app needs to drive itself",
            "'not authorized' / 'permission' appeared in the run artifacts",
        ],
        "next_diagnostic": "grant the macOS permission on the run lane (or move to a pre-granted lane) and re-run; this is a lane/auth gap, not a product bug",
        "retry_env": {
            "CLAWDND_RAM_PREFLIGHT_STRICT": "lane preflight strictness (set to fail fast on an unprepared lane)",
        },
    },
}

# Generic fallback used for an unknown bucket. Routed to INFRA/MEASUREMENT because an
# unrecognized bucket most often means the harness/measurement emitted something off-contract
# (a measurement gap), not a confirmed product defect.
_UNKNOWN = {
    "causes": [
        "unrecognized failure bucket — not one of the ten canonical APP_FAILURE_BUCKETS",
        "likely a harness/measurement contract drift (a renamed or hand-written bucket)",
    ],
    "next_diagnostic": "map this label back to a canonical bucket (see qa/app_failure_buckets.py APP_FAILURE_BUCKETS) before triaging",
    "retry_env": {},
}


def classify(bucket: str) -> str:
    """Return CLASS_INFRA or CLASS_PRODUCT for a bucket. Unknown -> INFRA/MEASUREMENT."""
    if bucket in PRODUCT_BUCKETS:
        return CLASS_PRODUCT
    if bucket in INFRA_BUCKETS:
        return CLASS_INFRA
    return CLASS_INFRA


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from ``path`` read-only; return {} on any problem (no crash)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - missing/garbage artifact degrades to no evidence.
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_dir_evidence(run_dir: Path) -> list[str]:
    """Surface the run's OWN recorded failure detail as evidence (read-only).

    Reads app-status.final.json then app-status.initial.json and pulls the readiness/health
    ``failure_bucket``/``failure_detail`` the engine recorded. Pure reader: it never writes.
    A missing dir or unreadable file yields an empty list, never an exception.
    """
    evidence: list[str] = []
    if not run_dir or not run_dir.exists() or not run_dir.is_dir():
        return evidence
    for name in ("app-status.final.json", "app-status.initial.json"):
        status = _read_json(run_dir / name)
        if not status:
            continue
        if status.get("ok") is False:
            evidence.append(f"{name}: ok=false (launcher/viewer reported unhealthy)")
        for section in ("readiness", "health"):
            blob = status.get(section)
            if not isinstance(blob, dict):
                continue
            fb = blob.get("failure_bucket")
            fd = blob.get("failure_detail")
            if isinstance(fb, str) and fb:
                line = f"{name} {section}.failure_bucket={fb}"
                if isinstance(fd, str) and fd.strip():
                    line += f": {fd.strip()}"
                evidence.append(line)
            elif isinstance(fd, str) and fd.strip():
                evidence.append(f"{name} {section}.failure_detail: {fd.strip()}")
    return evidence


def triage(bucket: str, run_dir: Path | None = None) -> dict[str, Any]:
    """Triage a failure bucket into an actionable report (pure reader).

    Returns a dict with: bucket, known, classification, likely_causes, next_diagnostic,
    retry_env, evidence. An unknown bucket degrades to a generic report flagged ``known: false``.
    """
    known = bucket in APP_FAILURE_BUCKETS
    entry = _TRIAGE.get(bucket, _UNKNOWN)
    report: dict[str, Any] = {
        "bucket": bucket,
        "known": known,
        "classification": classify(bucket),
        "likely_causes": list(entry["causes"]),
        "next_diagnostic": entry["next_diagnostic"],
        "retry_env": dict(entry["retry_env"]),
        "evidence": _run_dir_evidence(run_dir) if run_dir is not None else [],
    }
    return report


def render_human(report: dict[str, Any]) -> str:
    """Render a triage report as a compact human-readable block."""
    lines: list[str] = []
    known = "canonical" if report["known"] else "UNKNOWN bucket (degraded report)"
    lines.append(f"bucket: {report['bucket']}  [{known}]")
    lines.append(f"classification: {report['classification']}")
    lines.append("likely causes:")
    for cause in report["likely_causes"]:
        lines.append(f"  - {cause}")
    lines.append(f"next diagnostic: {report['next_diagnostic']}")
    if report["retry_env"]:
        lines.append("retry env vars:")
        for k, v in report["retry_env"].items():
            lines.append(f"  {k}={v!r}")
    else:
        lines.append("retry env vars: (none — re-running with env won't fix a product defect)")
    if report["evidence"]:
        lines.append("run-dir evidence:")
        for ev in report["evidence"]:
            lines.append(f"  - {ev}")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Turn a WorldOS app failure bucket into an actionable triage (read-only)."
    )
    ap.add_argument("--bucket", required=True, help="failure bucket name (see qa/app_failure_buckets.py)")
    ap.add_argument("--run", help="optional run dir to read app-status evidence from (READ-ONLY)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a human report")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_dir = Path(args.run) if args.run else None
    report = triage(args.bucket, run_dir=run_dir)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_human(report))
    return 0


# Re-export the canonical tuple at module scope so callers/tests can rely on a single source.
__all__ = [
    "APP_FAILURE_BUCKETS",
    "CLASS_INFRA",
    "CLASS_PRODUCT",
    "INFRA_BUCKETS",
    "PRODUCT_BUCKETS",
    "classify",
    "triage",
    "render_human",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
