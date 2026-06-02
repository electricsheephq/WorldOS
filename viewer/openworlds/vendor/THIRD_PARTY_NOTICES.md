# OpenWorlds Vendored Runtime Notices

This directory vendors browser runtime assets so the packaged local app can load
the OpenWorlds surface without network CDN calls.

## React

- Files:
  - `react-18.3.1.development.js`
  - `react-dom-18.3.1.development.js`
- Version: 18.3.1
- Source: `https://unpkg.com/react@18.3.1/umd/react.development.js`,
  `https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js`
- License: MIT
- SHA256:
  - `react-18.3.1.development.js`: `28348fef6cb0ed8b2ceeb22deaf824428fd13875d84c73d38f77dd216fc24e7f`
  - `react-dom-18.3.1.development.js`: `f9044a5e9c39db8bb1a204dff924e526ec0a621e695bb69de1035811be8709e4`

React's license is included in the upstream distribution header. This file and
the project-level `THIRD_PARTY_NOTICES.md` record why the local copy is bundled.

## Babel Standalone

- File: `babel-standalone-7.29.0.min.js`
- Version: 7.29.0
- Source: `https://unpkg.com/@babel/standalone@7.29.0/babel.min.js`
- License: MIT
- SHA256: `2623a9e22809915ce789b4461154e277ddce520d5a4320c14d44332a5d0dcea0`

Babel's license is included in the upstream package. This file and the
project-level `THIRD_PARTY_NOTICES.md` record why the local copy is bundled.

## Phaser

- File: `phaser-3.80.1.min.js`
- Version: 3.80.1
- Source: `https://cdn.jsdelivr.net/npm/phaser@3.80.1/dist/phaser.min.js`
- License: MIT
- SHA256: `62081f6a1b51d040473f919ffedef9009953c20833518e146da5a8d1c9405ea8`

Phaser is the 2D renderer for the OpenWorlds graphics tiers (the `render/`
thin-client; graphics roadmap `docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md`).
Vendored locally so the packaged app makes no runtime CDN call — same policy as
the React + Babel runtimes above. Phaser's MIT license is included in the
upstream distribution header.

## Google Fonts

- CSS: `google-fonts.css`
- Font families:
  - Cinzel - OFL - `https://github.com/NDISCOVER/Cinzel`
  - Cormorant Garamond - OFL - `https://github.com/CatharsisFonts/Cormorant`
  - IM Fell English - OFL - `https://github.com/librefonts/imfellenglish`
  - JetBrains Mono - OFL - `https://github.com/JetBrains/JetBrainsMono`
- Source: Google Fonts CSS generated from the prototype's declared font URL,
  with font files stored locally under `fonts/`.
- License: The listed font families are published under the SIL Open Font
  License. Keep these files local to preserve packaged-app fidelity and avoid
  runtime network calls.

Release hardening may replace the development UMD/Babel runtime with a compiled
bundle, but the packaged app must remain network-independent.
