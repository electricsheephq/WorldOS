# Release automation — auto-tag on milestone close (Versioning Phase-2)

The automation behind the owner's standing rule **"complete a milestone → tag/release"**. When a
GitHub milestone is closed, a workflow evaluates whether that milestone is allowed to be tagged and
— only when explicitly enabled — creates the annotated git tag + a `gh release`.

It is **DRAFT / dry-run-by-default / RELEASE-gated**: merging it does not arm it. It cannot tag
anything until an owner flips it live (see [Promoting from dry-run to live](#promoting-from-dry-run-to-live)).

| Piece | What it is |
|---|---|
| `.github/workflows/release-on-milestone-close.yml` | the workflow: trigger → gate → (dry-run print \| real tag+release) |
| `qa/release_gate_check.py` | the **unit-tested** gate decision (READ-ONLY): marker + clean tag + version consistency + STATUS:RELEASE |
| `qa/test_release_gate_check.py` | the gate's unit tests + a structural lint of the workflow YAML |
| `qa/generate_release_notes.py` | (Phase-1) the release-notes body + the DEVELOPMENT-vs-RELEASE banner |

## The `[release-ready]` marker convention (solving the no-milestone-labels problem)

GitHub milestones **do not support labels** — there is no native "this milestone is releasable" flag.
So the opt-in is an explicit text marker:

> Put the literal token **`[release-ready]`** in the milestone's **title or description** when (and
> only when) you intend its closure to cut a release.

- The marker is matched **case-insensitively**, with **literal brackets** (so a stray "release ready"
  in prose can't trip it).
- A milestone closed **without** the marker is treated as a **development** milestone: the workflow
  logs why and **exits without tagging**. Closing a milestone is *not*, by itself, consent to release.
- The marker is necessary but **not sufficient** — the four other gate conditions below must also hold.

### The full gate (all must hold for a GO)

1. **Marker** `[release-ready]` present in the milestone title/description.
2. **Clean version tag from the title** — the milestone title must be a clean `vX.Y.Z`
   (an optional `-rcN` / `-<suffix>` pre-release is allowed). `Sprint 12`, `v1.0`, `v1.0.5 (final)`
   are all refused.
3. **Tag does not already exist** — never clobber / re-cut an existing release.
4. **Version consistency** — the milestone's base `X.Y.Z` must equal the repo-root `VERSION` file
   **and** `servers/engine/__version__.py` (the single source of truth). Bump the source first.
5. **STATUS: RELEASE** — `qa/generate_release_notes.py` / the per-gate verdict must report **all 11
   RRI gates PASSED**. A `DEVELOPMENT` status (any gate SKIPPED/FAILED/MISSING/UNKNOWN) is a NO-GO.
   - A **pre-release** (`-rcN`) MAY ship on `DEVELOPMENT` status, but **only** when the run is
     dispatched with `allow_prerelease_dev=true`. A clean **GA** (no `-rc`) **always** requires
     `RELEASE`.

> **Where does the RELEASE status come from?** From a real per-gate RRI artifact
> (`release_readiness_verdict.json` or a `release_readiness.py` `RRI.json`) passed to the gate via
> `--verdict-json` / `--rri-json`. No such artifact is committed in the repo, so on a bare run the
> status **falls back to ledger-inference, which can never certify RELEASE** (the honesty guard). To
> actually GO you must point the gate at an all-PASS RRI artifact from a real Release-Readiness sweep.
> This is deliberate: a GO requires real release evidence, not merely a closed milestone.

## Dry-run safety (why merging this is safe)

- The `dry_run` input **defaults to `true`**. On a real `milestone: closed` event there is no input,
  so dry-run is true unless the owner has set the repo variable `RELEASE_AUTOMATION_LIVE=true`.
- In dry-run the workflow runs the **full** resolution + generates the notes and prints **exactly what
  it would tag/release** — but creates **nothing** (no tag, no release).
- Only an explicit `workflow_dispatch` with `dry_run=false`, **or** `RELEASE_AUTOMATION_LIVE=true`,
  reaches the real `git tag` + `gh release create` step.
- The real step re-confirms the tag is still free immediately before cutting it (defense-in-depth).
- Minimal permissions: `contents: write` only (for tags/releases). The job runs **no gameplay/heavy
  job**, reads the scores ledger **READ-ONLY**, and **never touches Eva or any gateway**.

## Promoting from dry-run to live

**Requires owner sign-off.** Recommended order:

1. **One successful dry-run on a real closed milestone.** Close a `[release-ready]` milestone whose
   title matches the bumped `VERSION`, with an all-PASS RRI artifact wired in. Confirm the workflow
   reaches the **DRY-RUN** step and prints the intended tag/release with no errors.
2. **Choose how to go live:**
   - *Per-cut (safer):* `gh workflow run "release-on-milestone-close.yml" -f milestone=vX.Y.Z -f dry_run=false`
     — a one-off real cut, dry-run stays the default for everything else.
   - *Standing (owner opt-in):* set the repo variable `RELEASE_AUTOMATION_LIVE=true`
     (`gh variable set RELEASE_AUTOMATION_LIVE --body true`) so milestone-close events auto-tag.
3. **Verify** the tag + `gh release` were created (pre-release iff the version has `-rc`), and that the
   release body is the generated notes.

To re-disarm: unset the repo variable (`gh variable delete RELEASE_AUTOMATION_LIVE`); dispatch runs
default back to dry-run.

## Invariants

- READ-ONLY on `qa/scores.db` (the engine stays the sole state writer); the workflow never commits or
  mutates the ledger.
- No gameplay/heavy job; never touches Eva or any gateway; uses the repo's `GITHUB_TOKEN`.
- The gate decision lives in `qa/release_gate_check.py` (unit-tested), not in ad-hoc workflow shell.
