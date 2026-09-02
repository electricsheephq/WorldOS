#!/usr/bin/env bash
# WorldOS Unity project autosave (owner-requested 2026-07-16). Primary save = LOCAL git commit
# (survives independent of GitHub quota/auth). GitHub push is BEST-EFFORT (starts working the
# moment LFS quota + creds are green). Runs as the unity user via cron.
#   worldos-unity-save.sh          -> commit-if-dirty only (frequent)
#   worldos-unity-save.sh --push   -> commit + best-effort LFS push (daily)
set -uo pipefail
export HOME=/home/unity
cd /home/unity/worldos-unity || exit 1
LOG=/home/unity/worldos-autosave.log
ts() { date -u +%FT%TZ; }
# Don't capture a mid-write tree: if any source file changed in the last 90s, Unity may be writing.
if find Assets Packages ProjectSettings -type f -newermt '-90 seconds' 2>/dev/null | head -1 | grep -q .; then
  echo "$(ts) skip: source modified <90s ago (Unity may be mid-write)" >>"$LOG"; exit 0
fi
if [ -z "$(git status --porcelain)" ]; then
  echo "$(ts) clean: nothing to save" >>"$LOG"; exit 0
fi
git add -A
git commit -q -m "autosave $(ts): working-tree checkpoint" && echo "$(ts) committed locally" >>"$LOG"
if [ "${1:-}" = "--push" ]; then
  if git push origin main >>"$LOG" 2>&1; then echo "$(ts) pushed to GitHub" >>"$LOG"
  else echo "$(ts) push FAILED (LFS quota/creds?) — LOCAL COMMIT IS SAFE" >>"$LOG"; fi
fi
