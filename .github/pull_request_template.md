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


## Visual Evidence (required for any renderer / plate / QA-visual change)
**The owner browses PRs — frames must render INLINE, not just be linked.** For renderer / viewer /
plate / VFX / UX / animation changes:
- Commit 1-6 BEFORE/AFTER still frames to `qa/evidence/<number>/` **on this branch** (≤400KB each;
  JPEG fine for painterly plates) — local paths (`~/worldos-session-notes/...`) are INVISIBLE to
  reviewers, repo paths only.
- **Embed each frame with markdown image syntax so it renders inline in the PR view** —
  `![caption](../blob/<branch>/qa/evidence/<number>/frame.jpg)` or the repo-relative form GitHub
  resolves on this PR's branch. A bare path or a "see qa/evidence/123/" pointer does not satisfy this
  — if the owner has to click through to a file listing to see a pixel, the section is incomplete.
- Motion: a numbered frame series (2-6 stills) is primary and each still gets its own embedded image
  (agents read stills reliably); a GIF is optional, for humans, in addition to the stills.
- Include the deterministic pre-gate output (`qa/visual_pregate.py`) when the change touches
  placement/pose/scale, and the coherence-gate result (`qa/check_grid_paint_coherence.py`) when the
  change touches a room's authored geometry or manifest.
- Engine-only changes with no visual surface: test names + fast_gate line instead of this section.

---
_Merging note: required checks stuck at "expected/pending" with mergeState BLOCKED is the known repo-wide auto-merge hang (#1389), NOT a CI failure. Procedure: real checks green + threads resolved → `gh pr merge <n> --admin --squash` (docs/OPERATIONS.md "Merging")._
