#!/usr/bin/env bash
# Deterministic provider-contract smoke adapter for the native macOS app.
#
# This validates the provider launch environment and appends one harmless player
# move to a caller-provided TEMP move sink. It deliberately does not start Claude,
# Codex, OpenClaw, engine servers, or any narrative QA harness.
set -euo pipefail

python3 - <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path


ALLOWED_PROVIDERS = {"claude", "codex", "openclaw"}
REQUIRED_NONEMPTY_ENV = (
    "WORLDOS_PROVIDER",
    "WORLDOS_WORLD",
    "WORLDOS_RUN_ID",
    "WORLDOS_PLAY_PORT",
    "WORLDOS_PLAYER_MOVES",
)
REQUIRED_PRESENT_ENV = ("WORLDOS_PLAY_COMPANIONS",)
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "COOKIE")


def fail(message: str, code: int = 2) -> None:
    print(f"provider contract smoke failed: {message}", file=sys.stderr)
    raise SystemExit(code)


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"missing required env {name}")
    return value


def is_temp_child(path: Path) -> bool:
    resolved = path.expanduser().resolve(strict=False)
    candidates = {Path(tempfile.gettempdir()).resolve(strict=False)}
    tmpdir = os.environ.get("TMPDIR", "").strip()
    if tmpdir:
        candidates.add(Path(tmpdir).resolve(strict=False))
    candidates.update(
        p.resolve(strict=False)
        for p in (Path("/tmp"), Path("/private/tmp"), Path("/var/folders"))
        if p.exists()
    )
    for root in candidates:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


missing = [name for name in REQUIRED_NONEMPTY_ENV if not os.environ.get(name, "").strip()]
missing.extend(name for name in REQUIRED_PRESENT_ENV if name not in os.environ)
if missing:
    fail("missing required env: " + ", ".join(missing))

provider = env_required("WORLDOS_PROVIDER").lower()
if provider not in ALLOWED_PROVIDERS:
    fail(f"unknown provider {provider!r}; expected one of {sorted(ALLOWED_PROVIDERS)}")

world = env_required("WORLDOS_WORLD")
run_id = env_required("WORLDOS_RUN_ID")
port_raw = env_required("WORLDOS_PLAY_PORT")
try:
    port = int(port_raw)
except ValueError:
    fail(f"WORLDOS_PLAY_PORT must be an integer, got {port_raw!r}")
if not (1 <= port <= 65535):
    fail(f"WORLDOS_PLAY_PORT out of range: {port}")

moves_path = Path(env_required("WORLDOS_PLAYER_MOVES"))
if moves_path.exists() and moves_path.is_dir():
    fail(f"WORLDOS_PLAYER_MOVES points at a directory: {moves_path}")
if not is_temp_child(moves_path):
    fail(
        "WORLDOS_PLAYER_MOVES must be under a temp directory for smoke mode "
    )

move = {
    "kind": "clarify",
    "text": "provider contract smoke: confirm launch environment and move sink wiring",
}
moves_path.parent.mkdir(parents=True, exist_ok=True)
with moves_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(move, separators=(",", ":")) + "\n")

companions = os.environ.get("WORLDOS_PLAY_COMPANIONS", "")
summary = {
    "ok": True,
    "provider": provider,
    "world": world,
    "run_id": run_id,
    "port": port,
    "companion_count": len([item for item in companions.split(",") if item.strip()]),
    "move_path": str(moves_path.expanduser().resolve(strict=False)),
    "redacted_env_keys": sorted(
        key for key in os.environ if any(marker in key.upper() for marker in SECRET_MARKERS)
    ),
}
print(json.dumps(summary, indent=2, sort_keys=True))
PY
