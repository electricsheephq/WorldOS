# OpenWorlds Surface Source Contract

This directory hosts the first exact-fidelity OpenWorlds browser surface for the
local viewer. It is intentionally a viewer asset bundle, not a new engine,
rules, voice, or campaign-state authority.

## Source Artifact

- Source artifact: `OpenWorlds.zip`
- Source SHA256: `8e9e2b885764fd3492b74b2d02eda5db9827eb087121054e8ca52e9ace10fd0a`
- Extraction label used during review: `openworlds-design-2026-05-25/openworlds`
- Provenance: internal design handoff for ClawDnD/OpenWorlds implementation, not
  a blanket license for third-party screenshots, uploads, or generated images.

## Included Source Files

The production viewer route maps source files into this directory as follows:

| Source artifact entry | Repo path |
| --- | --- |
| `openworlds/Open Worlds.html` | `viewer/openworlds/index.html` |
| `openworlds/styles.css` | `viewer/openworlds/styles.css` |
| `openworlds/data.js` | `viewer/openworlds/data.js` |
| `openworlds/app.jsx` | `viewer/openworlds/app.jsx` |
| `openworlds/chrome.jsx` | `viewer/openworlds/chrome.jsx` |
| `openworlds/tooltip.jsx` | `viewer/openworlds/tooltip.jsx` |
| `openworlds/toast.jsx` | `viewer/openworlds/toast.jsx` |
| `openworlds/camp-sidebar.jsx` | `viewer/openworlds/camp-sidebar.jsx` |
| `openworlds/screen-*.jsx` | `viewer/openworlds/screen-*.jsx` |

## Intentional Exclusions

- `openworlds/tweaks-panel.jsx` is excluded from the production route. The app
  still keeps the prototype tweak defaults in `app.jsx`, but no Open Design
  sandbox panel is loaded.
- `openworlds/screenshots/*`, uploaded images, and reference captures are not
  included. They remain reference-only unless a later PR clears provenance.
- Live CDN runtime imports are not used. React, ReactDOM, Babel standalone, and
  the Google font files required by the prototype are vendored under
  `viewer/openworlds/vendor/`.

## State Boundary

For this PR, `data.js` is non-canonical demo data used only to render the exact
surface. Future PRs replace those rows with read-only viewer APIs. The browser
may read viewer endpoints and, once backed actions exist, post player intent to
`POST /move`; it must not write `snapshot.json`, `play-state`, `qa/state`,
inventory, quests, XP, world clocks, or companion state directly.
