# qa/evidence/ — committed visual evidence (the evidence rule)

Owner-ratified 2026-07-08: **visual claims need pixels reviewers can SEE.** Issues and PRs about
anything graphic/animated (actors, plates, poses, lighting, UI) must carry still-frame evidence
that is visible to human AND agent reviewers — which means committed here (or drag-dropped into
the GitHub issue), never a local machine path.

Rules:
- Directory per issue/PR: `qa/evidence/<number>/` — BEFORE/AFTER stills, numbered series for motion.
- ≤400KB per frame (JPEG for painterly plates, PNG for UI); ≤6 frames per change; GIF optional
  (≤2MB) — stills are the primary artifact because agents read stills reliably.
- Pair frames with the deterministic manifest/pre-gate output when available
  (qa/visual_pregate.py) so the evidence is measured, not just eyeballed.
- These files merge with the PR — they ARE the documented visual history of the renderer.
Templates: `.github/ISSUE_TEMPLATE/graphics-defect.yml` enforces this on issues; the PR template's
"Evidence" section enforces it on PRs.
