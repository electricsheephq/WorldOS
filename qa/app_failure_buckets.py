#!/usr/bin/env python3
"""WorldOS built-app failure bucket classification.

These buckets are intentionally small and stable so agents can route failures
without parsing screenshots. Callers may pass partial evidence; missing evidence
falls back to the crispest safe bucket instead of crashing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_FAILURE_BUCKETS = (
    "no_app",
    "no_launcher",
    "no_provider",
    "no_art",
    "no_actor",
    "no_actions",
    "move_rejected",
    "no_narration",
    "console_error",
    "permission_prompt",
)


@dataclass(frozen=True)
class Classification:
    bucket: str
    detail: str

    def as_pair(self) -> str:
        detail = self.detail.replace("|", "/").replace("\n", " ").replace("\r", " ").strip()
        return f"{self.bucket}|{detail}"


def load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def bucket_pair(bucket: str, detail: str) -> Classification:
    if bucket not in APP_FAILURE_BUCKETS:
        bucket = "no_provider"
    return Classification(bucket=bucket, detail=detail)


def classify_native_failure(
    *,
    result: str,
    can_act: Any,
    surface: dict[str, Any] | None = None,
    app_status: dict[str, Any] | None = None,
) -> Classification:
    surface = surface or {}
    app_status = app_status or {}
    readiness = app_status.get("readiness") if isinstance(app_status.get("readiness"), dict) else {}
    health = app_status.get("health") if isinstance(app_status.get("health"), dict) else {}
    if isinstance(readiness.get("failure_bucket"), str) and readiness.get("failure_bucket") in APP_FAILURE_BUCKETS:
        return bucket_pair(str(readiness["failure_bucket"]), str(readiness.get("failure_detail") or "app-status readiness check failed"))
    if isinstance(health.get("failure_bucket"), str) and health.get("failure_bucket") in APP_FAILURE_BUCKETS:
        return bucket_pair(str(health["failure_bucket"]), str(health.get("failure_detail") or "app-status health check failed"))

    art = app_status.get("art") if isinstance(app_status.get("art"), dict) else {}
    live = app_status.get("live") if isinstance(app_status.get("live"), dict) else {}
    actor = live.get("actor") if isinstance(live.get("actor"), dict) else {}
    enabled_count = live.get("enabled_action_count")
    viewer = app_status.get("viewer") if isinstance(app_status.get("viewer"), dict) else {}
    chat_lines = viewer.get("chat_lines")

    if result in {"build_failed", "app_not_running"}:
        return bucket_pair("no_app", "WorldOS.app did not build, launch, or remain running")
    if result == "no_launcher":
        return bucket_pair("no_launcher", "launcher viewer did not answer /openworlds/ and /app-status on the same port")
    if app_status and app_status.get("ok") is False:
        return bucket_pair("no_launcher", "app-status reported an unhealthy launcher/viewer")
    if art.get("private_root_present") is False:
        return bucket_pair("no_art", "private art root was not present in app-status")
    if not truthy(can_act):
        return bucket_pair("no_provider", "no minted live provider viewer reported can_act:true")
    if not actor.get("id") and not actor.get("name"):
        return bucket_pair("no_actor", "app-status did not report an active player actor")
    if enabled_count == 0:
        return bucket_pair("no_actions", "app-status reported zero enabled player actions")
    if chat_lines == 0:
        return bucket_pair("no_narration", "app-status reported no chat/narration lines")

    surface_actor = ((surface.get("actionModel") or {}).get("actor") or {}) if isinstance(surface, dict) else {}
    if truthy(can_act) and not (surface_actor.get("id") or surface_actor.get("name") or actor.get("id") or actor.get("name")):
        return bucket_pair("no_actor", "session-surface did not report an active player actor")
    return bucket_pair("no_provider", "native transition failed without a more specific bucket")


def classify_part_b_readiness_failure(*, saw_canact: Any, saw_pc: Any, chat_lines: int) -> Classification:
    if not truthy(saw_canact):
        return bucket_pair("no_provider", "faithful backend never exposed can_act:true")
    if not truthy(saw_pc):
        return bucket_pair("no_actor", "faithful backend never seated a player character")
    if int(chat_lines or 0) <= 0:
        return bucket_pair("no_narration", "faithful backend produced no opening narration")
    return bucket_pair("no_actions", "faithful backend was not player-ready")


def _grep_any(paths: list[Path], pattern: str) -> bool:
    rx = re.compile(pattern, re.IGNORECASE)
    for path in paths:
        if not path.exists() or path.is_dir():
            continue
        try:
            if rx.search(path.read_text(encoding="utf-8", errors="ignore")):
                return True
        except OSError:
            continue
    return False


def classify_part_b_failure_from_artifacts(run_dir: Path, fallback_result: str = "FAIL") -> Classification:
    paths = [
        run_dir / "console.ndjson",
        run_dir / "network.ndjson",
        run_dir / "actions.ndjson",
        run_dir / "summary.md",
        run_dir / "player" / "console.ndjson",
        run_dir / "player" / "network.ndjson",
    ]
    if _grep_any(paths, r"permission|not authorized|accessibility|screen recording|AXIsProcessTrusted"):
        return bucket_pair("permission_prompt", "macOS permission prompt or accessibility/screen-recording denial appeared")
    if _grep_any(paths, r"console_error|pageerror|uncaught|exception"):
        return bucket_pair("console_error", "browser console/page error recorded during app playtest")
    if _grep_any(paths, r"move_rejected|/move.*(4[0-9][0-9]|5[0-9][0-9])|move not sent|rejected"):
        return bucket_pair("move_rejected", "player move was rejected or failed to reach /move")
    return bucket_pair("no_provider", f"part B failed: {fallback_result}")


def classify_part_b_score_failure(score_path: Path) -> Classification:
    try:
        score = json.loads(score_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - surfaced in the failure detail.
        return bucket_pair("no_provider", f"score.json pass=false and score could not be read: {exc}")
    if not isinstance(score, dict):
        return bucket_pair("no_provider", "score.json pass=false and score was not an object")

    console_errors = int(score.get("console_errors") or 0)
    critical = int(score.get("bug_reports_critical") or 0)
    satisfaction = score.get("persona_satisfaction")
    if console_errors > 0:
        return bucket_pair("console_error", f"score.json failed: console_errors={console_errors}")
    if critical > 0:
        return bucket_pair("no_provider", f"score.json failed: critical_bug_reports={critical}")
    if not score.get("completed_intro_flow"):
        if score.get("reached_play_screen"):
            return bucket_pair("no_actions", "score.json failed: player reached the table but submitted no in-story turn")
        return bucket_pair("no_actions", "score.json failed: player never reached the playable table")
    if score.get("gave_up"):
        detail = str(score.get("give_up_reason") or "player gave up").strip()
        return bucket_pair("no_provider", f"score.json failed: {detail}")
    if isinstance(satisfaction, (int, float)) and satisfaction < 6:
        return bucket_pair("no_provider", f"score.json failed: satisfaction={satisfaction}/10")
    return bucket_pair("no_provider", "score.json pass=false without a more specific signal")


def classify_browser_probe(*, tab_url: str, app_status_ok: Any, status_url: str = "") -> Classification | None:
    """Return no_launcher when a visible browser tab is stale or unbacked.

    A screenshot of /openworlds/ is only evidence when the same localhost port
    answers /app-status. Without that probe, a cached rendered page can fool the
    harness into accepting a dead app.
    """
    if truthy(app_status_ok):
        return None
    detail = "browser tab is visible but same-port /app-status is unreachable"
    if tab_url:
        detail += f" (tab={tab_url})"
    if status_url:
        detail += f" (status={status_url})"
    return bucket_pair("no_launcher", detail)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify WorldOS built-app harness failures.")
    sub = parser.add_subparsers(dest="command", required=True)

    native = sub.add_parser("native")
    native.add_argument("--result", required=True)
    native.add_argument("--can-act", default="false")
    native.add_argument("--surface-json", default="{}")
    native.add_argument("--app-status-json", default="{}")

    ready = sub.add_parser("part-b-readiness")
    ready.add_argument("--saw-canact", default="0")
    ready.add_argument("--saw-pc", default="0")
    ready.add_argument("--chat-lines", type=int, default=0)

    artifacts = sub.add_parser("part-b-artifacts")
    artifacts.add_argument("--run-dir", required=True)
    artifacts.add_argument("--fallback-result", default="FAIL")

    score = sub.add_parser("part-b-score")
    score.add_argument("--score-json", required=True)

    browser = sub.add_parser("browser-probe")
    browser.add_argument("--tab-url", default="")
    browser.add_argument("--status-url", default="")
    browser.add_argument("--app-status-ok", default="false")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "native":
        result = classify_native_failure(
            result=args.result,
            can_act=args.can_act,
            surface=load_json(args.surface_json),
            app_status=load_json(args.app_status_json),
        )
    elif args.command == "part-b-readiness":
        result = classify_part_b_readiness_failure(
            saw_canact=args.saw_canact,
            saw_pc=args.saw_pc,
            chat_lines=args.chat_lines,
        )
    elif args.command == "part-b-artifacts":
        result = classify_part_b_failure_from_artifacts(Path(args.run_dir), args.fallback_result)
    elif args.command == "part-b-score":
        result = classify_part_b_score_failure(Path(args.score_json))
    else:
        result = classify_browser_probe(
            tab_url=args.tab_url,
            status_url=args.status_url,
            app_status_ok=args.app_status_ok,
        ) or bucket_pair("no_provider", "browser probe passed")
    print(result.as_pair())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
