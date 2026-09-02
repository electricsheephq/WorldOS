#!/bin/bash
if [ "${WORLDOS_ALLOW_RETIRED_HOST:-0}" != "1" ]; then
  echo "GEX44 retired 2026-08-06 — see docs/GEX44-RETIRED.md" >&2
  exit 2
fi

export HOME=/home/unity DISPLAY=:0 XDG_RUNTIME_DIR=/tmp/runtime-unity
export PATH=/usr/local/bin:/usr/bin:/bin:/home/unity/.local/bin
cd /home/unity
exec /home/unity/Unity/Hub/Editor/6000.5.1f1/Editor/Unity -projectPath /home/unity/worldos-unity
