#!/bin/bash
export HOME=/home/unity DISPLAY=:0 XDG_RUNTIME_DIR=/tmp/runtime-unity
export PATH=/usr/local/bin:/usr/bin:/bin:/home/unity/.local/bin
cd /home/unity
exec /home/unity/Unity/Hub/Editor/6000.5.1f1/Editor/Unity -projectPath /home/unity/worldos-unity
