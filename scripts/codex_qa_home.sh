#!/usr/bin/env bash
# Build the isolated Codex home used by the WorldOS GPT-DM fair-test lane.
#
# This restores a lean Codex CLI environment after --ignore-user-config was
# removed. It writes only a minimal config and symlinks the operator's existing
# auth.json in place. It never copies credentials.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$HOME/.codex-worldos-qa}"
REPO="${2:-$ROOT}"

mkdir -p "$DEST"

python3 - "$DEST/config.toml" "$REPO" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1]).expanduser()
repo = str(Path(sys.argv[2]).expanduser().resolve(strict=False))
out.write_text(
    "\n".join(
        [
            'approval_policy = "never"',
            'sandbox_mode = "read-only"',
            "",
            f"[projects.{json.dumps(repo)}]",
            'trust_level = "trusted"',
            "",
        ]
    ),
    encoding="utf-8",
)
PY

if [ -f "$HOME/.codex/auth.json" ]; then
  ln -sf "$HOME/.codex/auth.json" "$DEST/auth.json"
else
  echo "[codex-qa-home] warning: $HOME/.codex/auth.json not found; run codex login before fair-test runs" >&2
fi

echo "CODEX_HOME=$DEST"
echo "Run: CODEX_HOME=$DEST codex login status"
