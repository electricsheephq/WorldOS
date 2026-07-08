# Pull Request

## Summary

Describe what changed and why.

## Linked Issue

Closes #

## Tracker / Milestone

- Tracker or milestone:
- Required? yes/no and why:

## Changes

- <summary>

## Licensing / CLA

- [ ] I have read `CLA.md` and submit this contribution under the WorldOS Contributor License Agreement.
- [ ] I have not included confidential information, private customer data, private imported content, or third-party-restricted material.
- [ ] Any third-party material in this PR is clearly identified with source and license.

## Review Thread State

- Current-head unresolved review threads:
- P0-P2 blocker count:
- P3/advisory count:
- Top-level bot comments requiring action:
- Check annotations requiring action:
- Bot rerun status:

## Validation

List the checks you ran.

## Release / WorldOS Proof Tier

- [ ] Not release-affecting
- [ ] Local or fast smoke only
- [ ] Staging artifact proof
- [ ] Release proof
- [ ] Runtime/customer proof
- [ ] WorldOS RRI/persona proof required

## Release Notes / Changelog

- Release-note impact:
- Draft human-readable entry or no-impact rationale:
- Verification/evidence tail needed? yes/no:

## Safety / Rollback

## Evidence

- Evidence path:

## Notes For Next Agent

- Exact next action:


## Evidence (required for anything visual)
For renderer / viewer / UX / animation changes: commit 1-6 BEFORE/AFTER still frames to
`qa/evidence/<issue-or-pr>/` **on this branch** (≤400KB each; JPEG fine for painterly plates) and
reference them here. Local paths (`~/worldos-session-notes/...`) are INVISIBLE to reviewers — repo
paths only. Motion: a numbered frame series (2-6 stills) is primary; a GIF is optional for humans
(agents read stills). Include the deterministic pre-gate output (qa/visual_pregate.py) when the
change touches placement/pose/scale. Engine-only changes: test names + fast_gate line instead.

---
_Merging note: required checks stuck at "expected/pending" with mergeState BLOCKED is the known repo-wide auto-merge hang (#1389), NOT a CI failure. Procedure: real checks green + threads resolved → `gh pr merge <n> --admin --squash` (docs/OPERATIONS.md "Merging")._
