# OpenWorlds Design Asset Policy

This policy governs any WorldOS work derived from the local OpenWorlds design
bundle. It exists to preserve visual fidelity without accidentally committing
uncleared reference art, prototype-only dependencies, private content, or
third-party game UI material.

## Source Buckets

Source artifact:

- `OpenWorlds.zip`
  - SHA256: `8e9e2b885764fd3492b74b2d02eda5db9827eb087121054e8ca52e9ace10fd0a`
  - Acquisition date: 2026-05-25 UTC
  - Local extraction label: `openworlds-design-2026-05-25`
  - License/provenance status: internal design handoff; not a blanket asset
    license

Primary visual/reference contract:

- `openworlds/Open Worlds.html`
- `openworlds/styles.css`
- `openworlds/app.jsx`
- `openworlds/chrome.jsx`
- `openworlds/screen-*.jsx`
- `openworlds/camp-sidebar.jsx`
- `openworlds/toast.jsx`
- `openworlds/tooltip.jsx`

Prototype/demo data requiring rewrite:

- `openworlds/data.js`

Open Design or tweak-host code to remove from production routes:

- `openworlds/tweaks-panel.jsx`

Secondary reference only:

- `uploads/dndforever/index.html`
- `uploads/dndforever/DESIGN-HANDOFF.md`
- `uploads/dndforever/DESIGN-MANIFEST.json`

Reference-only assets:

- `openworlds/screenshots/*`
- `uploads/*.png`
- `OpenWorlds.zip`

The reference-only assets must not be committed unless a follow-up PR documents
clear provenance and licensing.

When a later PR copies any source file into the repo, its PR body must map the
source artifact entry to the new repo-relative path and list the relevant
license/provenance note.

## Allowed In Repo

- Audited and cleaned HTML/CSS/JS implementation code from the primary
  OpenWorlds export only after the PR documents provenance, dependency notices,
  and copied source files.
- Abstract design tokens, layout structure, component behavior, and interaction
  patterns ported from the primary export.
- Locally vendored runtime libraries for the first exact-fidelity sprint, when
  their license notices are included and no network CDN calls remain.
- WorldOS-authored docs that describe source buckets, fidelity requirements,
  and asset handling.

## Not Allowed In Repo

- `OpenWorlds.zip`.
- Pasted Owlcat/BG/reference screenshots.
- Any image from `uploads/*.png` or `openworlds/screenshots/*` without explicit
  provenance.
- Open Design sandbox, preview, snapshot, or tweak-host chrome.
- Live CDN dependencies in the packaged app.
- Private world seeds, QA transcripts, play-state, secrets, or local runtime
  artifacts.
- Browser-side code that directly mutates campaign state.

## Prototype Dependency Policy

The OpenWorlds prototype currently references Google Fonts, React, ReactDOM, and
Babel through network CDNs. Production WorldOS builds must not depend on those
network calls.

The first exact-fidelity PR may use locally vendored React, ReactDOM, and Babel
to preserve the export quickly. A release-hardening PR must replace runtime
Babel with a proper bundled build before beta distribution or notarization.

Fonts must be self-hosted with license notices, replaced by system fallbacks with
explicit visual acceptance, or separately cleared before shipping.

## PR Checklist For Visual Work

Every OpenWorlds visual PR must state:

- Whether any binary/image assets were copied.
- Whether any third-party or reference assets were copied.
- Which source files from the primary export were ported.
- Whether live network dependencies remain.
- Which screenshot viewports were checked.
- Whether the browser surface still treats the engine as the sole game-state
  writer.

The default acceptable statement is:

> No third-party/reference image assets copied. OpenWorlds implementation code
> was audited against the local primary export before porting. Runtime
> dependencies are local and documented. Prototype data was rewritten or clearly
> kept as non-canonical demo data. Engine state remains read-only from the
> browser except for existing `/move` player-intent posts.
