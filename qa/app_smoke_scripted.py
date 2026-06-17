#!/usr/bin/env python3
"""Deterministic multi-beat WorldOS scripted-provider smoke.

This is a fast wiring proof for the real viewer/provider path. It does not
replace the release RRI gate. It launches the dev-gated scripted provider,
validates same-port /app-status, submits deterministic /move intents, and writes
one disk-backed evidence bundle.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qa.app_failure_buckets import classify_browser_probe  # noqa: E402

DEFAULT_ROOT = Path("/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability")
MAX_HTTP_BYTES = 8 * 1024 * 1024


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_ndjson(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


def fetch_json(url: str, *, timeout: float = 3.0) -> tuple[dict[str, Any], int]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_HTTP_BYTES + 1)
        if len(data) > MAX_HTTP_BYTES:
            raise ValueError(f"{url} response exceeded {MAX_HTTP_BYTES} bytes")
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{url} returned non-object JSON")
        return payload, int(getattr(resp, "status", 0) or 0)


def post_json(url: str, payload: dict[str, Any], *, timeout: float = 5.0) -> tuple[dict[str, Any], int]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(MAX_HTTP_BYTES + 1)
        parsed = json.loads(body.decode("utf-8")) if body else {}
        return parsed if isinstance(parsed, dict) else {}, int(getattr(resp, "status", 0) or 0)


def wait_for_status(base_url: str, out: Path, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = ""
    url = urllib.parse.urljoin(base_url, "/app-status")
    while time.time() < deadline:
        try:
            payload, status = fetch_json(url)
            append_ndjson(out / "network.ndjson", {"at": time.time(), "method": "GET", "url": url, "status": status})
            return payload
        except Exception as exc:  # noqa: BLE001 - evidence bucket wants the raw reason.
            last_error = str(exc)
            append_ndjson(out / "network.ndjson", {"at": time.time(), "method": "GET", "url": url, "error": last_error})
            time.sleep(0.5)
    raise RuntimeError(f"/app-status did not answer on {url}: {last_error}")


def surface_url(base_url: str, status: dict[str, Any]) -> str:
    endpoint = ((status.get("endpoints") or {}).get("session_surface") or "/session-surface") if isinstance(status.get("endpoints"), dict) else "/session-surface"
    url = urllib.parse.urljoin(base_url, str(endpoint))
    campaign = ((status.get("live") or {}).get("campaign_id") or "") if isinstance(status.get("live"), dict) else ""
    parsed = urllib.parse.urlparse(url)
    if campaign and not parsed.query:
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode({"campaign": campaign})))
    return url


def provider_summary(play_state: Path) -> dict[str, Any]:
    path = play_state / "scripted-provider" / "summary.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    trace = play_state / "scripted-provider" / "trace.ndjson"
    if not trace.exists():
        return {}
    events: list[dict[str, Any]] = []
    for line in trace.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return {
        "schema": "worldos.scripted-provider-summary.v1",
        "provider": "scripted",
        "deterministic": True,
        "model_free": True,
        "trace_exists": True,
        "event_count": len(events),
        "move_resolved_count": sum(1 for event in events if event.get("event") == "move_resolved"),
        "first_event": events[0] if events else None,
        "last_event": events[-1] if events else None,
    }


def copy_play_state(run_id: str, out: Path) -> None:
    play_state = ROOT / "play-state" / run_id
    if not play_state.exists():
        return
    dest = out / "play-state"
    for rel in ("chat.jsonl", "player_moves.jsonl", "viewer.log", "scripted-provider/trace.ndjson", "scripted-provider/summary.json"):
        source = play_state / rel
        if source.exists() and source.is_file():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    summary = provider_summary(play_state)
    if summary:
        json_dump(out / "scripted-provider" / "summary.json", summary)
        if (play_state / "scripted-provider" / "trace.ndjson").exists():
            (out / "scripted-provider").mkdir(parents=True, exist_ok=True)
            shutil.copy2(play_state / "scripted-provider" / "trace.ndjson", out / "scripted-provider" / "trace.ndjson")


def write_text_snapshot(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def html_text(base_url: str) -> str:
    try:
        with urllib.request.urlopen(urllib.parse.urljoin(base_url, "/openworlds/"), timeout=5) as resp:
            return resp.read(MAX_HTTP_BYTES).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"unavailable: {exc}\n"


def chrome_binary() -> str:
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ):
        if Path(candidate).exists():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def capture_openworlds_screenshot(
    *,
    base_url: str,
    out: Path,
    port: int,
    label: str,
    gaps: list[dict[str, str]],
    screenshots: list[str],
) -> None:
    chrome = chrome_binary()
    target = out / "screenshots" / f"{label}.png"
    if not chrome:
        gaps.append({"source": "screenshot", "kind": label, "path": str(target), "reason": "chrome_not_found"})
        return
    url = f"{base_url}/openworlds/#table"
    reasons: list[str] = []
    for attempt in range(1, 3):
        profile = out / ".chrome-profile" / f"{port}-{label}-{attempt}"
        profile.mkdir(parents=True, exist_ok=True)
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--window-size=1512,982",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--virtual-time-budget=7000",
            f"--screenshot={target}",
            url,
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        deadline = time.time() + 20
        while time.time() < deadline:
            if target.exists() and target.stat().st_size > 200:
                screenshots.append(str(target.relative_to(out)))
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                shutil.rmtree(profile, ignore_errors=True)
                return
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        if target.exists() and target.stat().st_size > 200:
            screenshots.append(str(target.relative_to(out)))
            shutil.rmtree(profile, ignore_errors=True)
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            proc.kill()
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        if target.exists() and target.stat().st_size > 200:
            screenshots.append(str(target.relative_to(out)))
            return
        reasons.append(f"attempt{attempt}:chrome_exit={proc.returncode}")
        time.sleep(0.5)
    gaps.append({"source": "screenshot", "kind": label, "path": str(target), "reason": "; ".join(reasons)})


def classify_status(status: dict[str, Any]) -> tuple[str, str]:
    readiness = status.get("readiness") if isinstance(status.get("readiness"), dict) else {}
    if readiness.get("failure_bucket") and readiness.get("failure_bucket") != "none":
        return str(readiness.get("failure_bucket")), str(readiness.get("failure_detail") or "app-status readiness failed")
    live = status.get("live") if isinstance(status.get("live"), dict) else {}
    art = status.get("art") if isinstance(status.get("art"), dict) else {}
    viewer = status.get("viewer") if isinstance(status.get("viewer"), dict) else {}
    actor = live.get("actor") if isinstance(live.get("actor"), dict) else {}
    if art.get("private_root_present") is False:
        return "no_art", "private art root missing"
    if not live.get("can_act"):
        return "no_provider", "scripted provider did not expose can_act:true"
    if not actor.get("id") and not actor.get("name"):
        return "no_actor", "no active actor in app-status"
    if int(live.get("enabled_action_count") or 0) <= 0:
        return "no_actions", "no enabled actions in app-status"
    if int(viewer.get("chat_lines") or 0) <= 0:
        return "no_narration", "no narration/chat lines in app-status"
    return "", ""


def run_smoke(args: argparse.Namespace) -> int:
    run_id = args.run_id or f"scripted-smoke-{utc_stamp()}"
    out = (Path(args.out).expanduser() if args.out else DEFAULT_ROOT / run_id).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for rel in ("screenshots", "a11y"):
        (out / rel).mkdir(parents=True, exist_ok=True)
    (out / "console.ndjson").write_text("", encoding="utf-8")
    (out / "network.ndjson").write_text("", encoding="utf-8")
    (out / "actions.ndjson").write_text("", encoding="utf-8")
    (out / "moves.ndjson").write_text("", encoding="utf-8")

    base_url = f"http://127.0.0.1:{int(args.port)}"
    env = os.environ.copy()
    env.update({
        "WORLDOS_ENABLE_SCRIPTED_PROVIDER": "1",
        "WORLDOS_PROVIDER": "scripted",
        "WORLDOS_PROVIDER_FAMILY": "scripted",
        "WORLDOS_AUTH_SURFACE": "dev-scripted",
        "WORLDOS_DM_MODEL": "scripted",
        "WORLDOS_ACTOR_MODEL": "scripted",
        "WORLDOS_SCORER_MODEL": "scripted",
        "WORLDOS_RUN_ID": run_id,
        "WORLDOS_WORLD": args.world,
        "WORLDOS_PLAY_PORT": str(args.port),
    })
    if args.art_root:
        env["WORLDOS_ART_REPO_ROOT"] = args.art_root
        env["WORLDOS_ART_REPO_ROOT"] = args.art_root

    log = (out / "run.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(ROOT / "scripts" / "play_scripted_dm.sh")],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    verdict = {
        "schema": "worldos.scripted-app-smoke.v1",
        "run_id": run_id,
        "world": args.world,
        "port": int(args.port),
        "beats_requested": int(args.beats),
        "status": "failed",
        "failure_bucket": "",
        "failure_detail": "",
        "screenshots": [],
        "evidence_gaps": [],
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    evidence_gaps: list[dict[str, str]] = verdict["evidence_gaps"]
    screenshots: list[str] = verdict["screenshots"]

    def fail(bucket: str, detail: str) -> int:
        verdict.update({"failure_bucket": bucket, "failure_detail": detail})
        json_dump(out / "smoke.json", verdict)
        return 1

    try:
        try:
            status = wait_for_status(base_url, out, timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            stale = classify_browser_probe(tab_url=f"{base_url}/openworlds/", status_url=f"{base_url}/app-status", app_status_ok=False)
            return fail(stale.bucket if stale else "no_launcher", str(exc))

        json_dump(out / "app-status.initial.json", status)
        write_text_snapshot(out / "a11y" / "initial.html", html_text(base_url))
        capture_openworlds_screenshot(base_url=base_url, out=out, port=int(args.port), label="initial", gaps=evidence_gaps, screenshots=screenshots)
        try:
            surface, _ = fetch_json(surface_url(base_url, status))
        except (OSError, urllib.error.URLError, ValueError) as exc:
            return fail("no_provider", f"initial session-surface fetch failed: {exc}")
        json_dump(out / "session-surface.initial.json", surface)
        bucket, detail = classify_status(status)
        if bucket:
            return fail(bucket, detail)

        last_chat_lines = int(((status.get("viewer") or {}).get("chat_lines") or 0) if isinstance(status.get("viewer"), dict) else 0)
        move_url = urllib.parse.urljoin(base_url, "/move")
        for beat in range(1, int(args.beats) + 1):
            move = {"kind": "do", "text": f"scripted smoke beat {beat}: inspect the lantern and keep moving."}
            append_ndjson(out / "moves.ndjson", {"at": time.time(), "beat": beat, "request": move})
            append_ndjson(out / "actions.ndjson", {"at": time.time(), "beat": beat, "action": "post_move", "url": move_url})
            try:
                response, http_status = post_json(move_url, move)
            except (OSError, urllib.error.URLError, ValueError) as exc:
                append_ndjson(out / "network.ndjson", {"at": time.time(), "method": "POST", "url": move_url, "error": str(exc)})
                verdict.update({"failure_bucket": "move_rejected", "failure_detail": str(exc)})
                json_dump(out / "smoke.json", verdict)
                return 1
            append_ndjson(out / "network.ndjson", {"at": time.time(), "method": "POST", "url": move_url, "status": http_status})
            if not response.get("ok"):
                verdict.update({"failure_bucket": "move_rejected", "failure_detail": str(response.get("reason") or response)})
                json_dump(out / "smoke.json", verdict)
                return 1
            deadline = time.time() + args.timeout
            advanced = False
            while time.time() < deadline:
                try:
                    status = wait_for_status(base_url, out, timeout=3)
                except Exception as exc:  # noqa: BLE001
                    return fail("no_launcher", f"app-status dropped during beat {beat}: {exc}")
                chat_lines = int(((status.get("viewer") or {}).get("chat_lines") or 0) if isinstance(status.get("viewer"), dict) else 0)
                summary = provider_summary(ROOT / "play-state" / run_id)
                if chat_lines > last_chat_lines and int(summary.get("resolved_move_count") or summary.get("move_resolved_count") or 0) >= beat:
                    advanced = True
                    last_chat_lines = chat_lines
                    break
                time.sleep(0.5)
            json_dump(out / f"app-status.beat-{beat}.json", status)
            try:
                surface, _ = fetch_json(surface_url(base_url, status))
            except (OSError, urllib.error.URLError, ValueError) as exc:
                return fail("no_provider", f"session-surface fetch failed after beat {beat}: {exc}")
            json_dump(out / f"session-surface.beat-{beat}.json", surface)
            write_text_snapshot(out / "a11y" / f"beat-{beat}.html", html_text(base_url))
            capture_openworlds_screenshot(base_url=base_url, out=out, port=int(args.port), label=f"beat-{beat:03d}", gaps=evidence_gaps, screenshots=screenshots)
            if not advanced:
                return fail("no_narration", f"narration did not advance after beat {beat}")

        try:
            final_status = wait_for_status(base_url, out, timeout=5)
        except Exception as exc:  # noqa: BLE001
            return fail("no_launcher", f"final app-status fetch failed: {exc}")
        try:
            final_surface, _ = fetch_json(surface_url(base_url, final_status))
        except (OSError, urllib.error.URLError, ValueError) as exc:
            return fail("no_provider", f"final session-surface fetch failed: {exc}")
        json_dump(out / "app-status.final.json", final_status)
        json_dump(out / "session-surface.final.json", final_surface)
        write_text_snapshot(out / "a11y" / "final.html", html_text(base_url))
        capture_openworlds_screenshot(base_url=base_url, out=out, port=int(args.port), label="final", gaps=evidence_gaps, screenshots=screenshots)
        copy_play_state(run_id, out)
        verdict.update({
            "status": "passed",
            "failure_bucket": "",
            "failure_detail": "",
            "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "beats_completed": int(args.beats),
            "app_status_url": f"{base_url}/app-status",
            "evidence_dir": str(out),
        })
        json_dump(out / "smoke.json", verdict)
        return 0
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        copy_play_state(run_id, out)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic multi-beat scripted WorldOS app smoke.")
    parser.add_argument("--beats", type=int, default=5)
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--out", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--world", default="baldurs-gate")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--art-root", default=os.environ.get("WORLDOS_ART_REPO_ROOT") or os.environ.get("WORLDOS_ART_REPO_ROOT") or "")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.beats < 1:
        raise SystemExit("--beats must be at least 1")
    return run_smoke(args)


if __name__ == "__main__":
    raise SystemExit(main())
