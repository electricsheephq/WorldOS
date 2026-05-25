# OpenWorlds Design Asset Policy

This policy governs any ClawDnD work derived from the local OpenWorlds design
bundle. It exists to preserve visual fidelity without accidentally committing
uncleared reference art, prototype-only dependencies, private content, or
third-party game UI material.

## Source Buckets

Primary visual/reference contract:

- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/Open Worlds.html`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/styles.css`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/app.jsx`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/chrome.jsx`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/screen-*.jsx`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/camp-sidebar.jsx`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/toast.jsx`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/tooltip.jsx`

Prototype/demo data requiring rewrite:

- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/data.js`

Open Design or tweak-host code to remove from production routes:

- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/tweaks-panel.jsx`

Secondary reference only:

- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/uploads/dndforever/index.html`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/uploads/dndforever/DESIGN-HANDOFF.md`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/uploads/dndforever/DESIGN-MANIFEST.json`

Reference-only assets:

- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/screenshots/*`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/uploads/*.png`
- `/Users/lume/Downloads/OpenWorlds.zip`

The reference-only assets must not be committed unless a follow-up PR documents
clear provenance and licensing.

## Allowed In Repo

- Audited and cleaned HTML/CSS/JS implementation code from the primary
  OpenWorlds export only after the PR documents provenance, dependency notices,
  and copied source files.
- Abstract design tokens, layout structure, component behavior, and interaction
  patterns ported from the primary export.
- Locally vendored runtime libraries for the first exact-fidelity sprint, when
  their license notices are included and no network CDN calls remain.
- ClawDnD-authored docs that describe source buckets, fidelity requirements,
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
Babel through network CDNs. Production ClawDnD builds must not depend on those
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
