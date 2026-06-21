#!/usr/bin/env bash
# Stuck-PR detector (SURFACE-ONLY — no auto-fix). Flags open, non-draft PRs that are stuck —
# a failing check, OR blocked/conflicting AND stale — then upserts a single living issue and
# @mentions the owner (→ GitHub notification email), and optionally emails via Resend if a
# RESEND_API_KEY is configured. Snooze a PR out of the report by labelling it `blocked`.
#
# Why surface-only: a scheduled workflow can't spawn an agent to fix the PR, so it makes the
# orphan VISIBLE to both the owner (email) and any future agent (the issue is a shared queue).
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-electricsheephq/WorldOS}"
OWNER_HANDLE="${OWNER_HANDLE:-100yenadmin}"
STALE_HOURS="${STALE_HOURS:-24}"
LABEL="stuck-pr-report"
cutoff=$(( $(date -u +%s) - STALE_HOURS * 3600 ))
ts=$(date -u +'%Y-%m-%d %H:%M')

# A PR is "stuck" if it has >=1 failing check, OR it is BLOCKED/DIRTY and hasn't been
# touched in >STALE_HOURS (so a PR merely mid-CI isn't flagged). Drafts and `blocked`-
# labelled PRs are excluded.
rows=$(gh pr list --repo "$REPO" --state open --limit 100 \
  --json number,title,url,isDraft,mergeStateStatus,updatedAt,author,labels,statusCheckRollup \
  | jq -r --argjson cutoff "$cutoff" '
    def isfail(s): (s=="FAILURE" or s=="ERROR" or s=="TIMED_OUT" or s=="CANCELLED" or s=="ACTION_REQUIRED");
    .[]
    | select(.isDraft | not)
    | select([.labels[].name] | index("blocked") | not)
    | ([.statusCheckRollup[]? | select(isfail(.conclusion // .state // ""))] | length) as $fail
    | ((.updatedAt | fromdateiso8601) < $cutoff) as $stale
    | select($fail > 0 or ((.mergeStateStatus == "DIRTY" or .mergeStateStatus == "BLOCKED") and $stale))
    | "- [#\(.number)](\(.url)) — \(.title) · `\(.mergeStateStatus)` · failing-checks: \($fail) · updated \(.updatedAt[0:10]) · @\(.author.login)"
  ')

if [ -z "$rows" ]; then
  echo "No stuck PRs as of $ts UTC. Nothing to report."
  exit 0
fi
count=$(printf '%s\n' "$rows" | grep -c '^- ' || true)

# Single-quoted printf format → literal backticks (no command substitution); %s carries values.
body=$(printf '🔧 **%s stuck PR(s)** as of %s UTC — each is failing a check, conflicting, or blocked & stale (>%sh). This report is **surface-only** (no auto-fix): pick one up, or label it `blocked` to snooze it out of this report.\n\n%s\n\n---\n_cc @%s · auto-maintained by `.github/workflows/stuck-pr-report.yml`. Agents: this issue is the shared queue of orphaned PRs — claim one and shepherd it through the worldos-dev merge gate._\n' \
  "$count" "$ts" "$STALE_HOURS" "$rows" "$OWNER_HANDLE")

# Upsert: keep ONE living issue (found by label), edit its body + add a dated comment to notify.
existing=$(gh issue list --repo "$REPO" --state open --label "$LABEL" --limit 1 --json number --jq '.[0].number // empty')
if [ -n "$existing" ]; then
  gh issue edit "$existing" --repo "$REPO" --body "$body" >/dev/null
  gh issue comment "$existing" --repo "$REPO" --body "↻ ${count} stuck PR(s) as of ${ts} UTC. cc @${OWNER_HANDLE}" >/dev/null
  num="$existing"
else
  url=$(gh issue create --repo "$REPO" --title "🔧 Stuck PRs — daily report" --label "$LABEL" --body "$body")
  num=$(printf '%s' "$url" | grep -oE '[0-9]+$')
fi
echo "Reported ${count} stuck PR(s) → issue #${num}."

# Optional Resend email — fires only if all three are set (zero-setup default is the @mention above).
if [ -n "${RESEND_API_KEY:-}" ] && [ -n "${RESEND_TO:-}" ] && [ -n "${RESEND_FROM:-}" ]; then
  html=$(printf '%s' "$body" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/$/<br>/')
  payload=$(jq -n --arg from "$RESEND_FROM" --arg to "$RESEND_TO" \
    --arg subj "[WorldOS] ${count} stuck PR(s)" --arg html "$html" \
    '{from:$from, to:[$to], subject:$subj, html:$html}')
  if curl -fsS -X POST https://api.resend.com/emails \
      -H "Authorization: Bearer ${RESEND_API_KEY}" -H "Content-Type: application/json" \
      -d "$payload" >/dev/null; then
    echo "Resend email sent to ${RESEND_TO}."
  else
    echo "::warning::Resend email failed (non-fatal)."
  fi
fi
