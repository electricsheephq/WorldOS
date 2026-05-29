# Third-Party Notices

WorldOS is source-available commercial software under the root `LICENSE` and
`ROYALTY-ADDENDUM.md`. This file records third-party material that WorldOS
bundles, adapts, or depends on, and the licensing decisions behind them.

## Bundled data

### System Reference Document 5.2 (SRD 5.2)
- **License:** Creative Commons Attribution 4.0 International (CC-BY-4.0)
- **Location:** `data/srd/`
- **Attribution:** see `data/srd/ATTRIBUTION.md` (required by the license)
- CC-BY-4.0 permits commercial use and is irrevocable. We treat the SRD as the
  canonical, shippable rules source.

## Runtime dependencies (permissive, installed from PyPI via uv)

WorldOS's MCP servers depend on these packages:
- **mcp** (MIT) — the Model Context Protocol SDK / FastMCP.
- **pydantic** (MIT) — state-model validation.
- **rapidfuzz** (MIT) — fuzzy matching for rules lookups.
- **httpx** (BSD-3-Clause) — HTTP client for the dnd5eapi.co fallback.
- **kokoro** (Apache-2.0) — local text-to-speech (added with the voice backend).

## Bundled browser runtime assets

The local OpenWorlds viewer surface vendors these files under
`viewer/openworlds/vendor/` so the packaged macOS app can render without live CDN
dependencies:

- **React 18.3.1** (`react-18.3.1.development.js`, MIT)
- **ReactDOM 18.3.1** (`react-dom-18.3.1.development.js`, MIT)
- **Babel Standalone 7.29.0** (`babel-standalone-7.29.0.min.js`, MIT)
- **Google Fonts families** (`Cinzel`, `Cormorant Garamond`,
  `IM Fell English`, `JetBrains Mono`, open font licenses)

See `viewer/openworlds/vendor/THIRD_PARTY_NOTICES.md` for source URLs and file
hashes. These development-mode runtime files are a fidelity bridge for the first
local packaged surface; release hardening should replace Babel-in-browser with a
compiled bundle while keeping all assets local.

## Bundled OpenWorlds icon assets

OpenWorlds vendors a small curated subset of **Game Icons** under
`viewer/openworlds/assets/icons/game-icons/` for gameplay affordances such as
attacks, dice, travel, quests, camp, settlements, consumables, and coins.

- **Source:** <https://game-icons.net> / `game-icons/icons`
- **License:** Creative Commons Attribution 3.0 (CC-BY-3.0), unless an upstream
  contributor is explicitly marked CC0.
- **Attribution:** see `viewer/openworlds/assets/icons/ATTRIBUTION.md`.
- **Authors in this subset:** Lorc, Delapouite, and Willdabeast.

The imported SVGs were normalized only for presentation by removing the opaque
background rectangle so the app can tint them as local CSS masks. WorldOS does
not vendor the full icon set and does not fetch icon assets from a network CDN.

## Structural references — learn-from only (NO code copied)

These projects informed WorldOS's design. After verifying their licensing
(2026-05-22), we copy **no code** from any of them; WorldOS's engine, rules, and
voice code is a clean-room implementation from the SRD and our own D&D 5e
knowledge.

- **study-flamingo/gamemaster-mcp** — declares MIT in `pyproject.toml` + README,
  **but the referenced `LICENSE.md` is absent from the repo** (verified
  2026-05-22), so no license is auto-detectable. Used only as a structural
  reference for the entity layout (Campaign / Party / Character / Combat / NPC /
  Quest / Location).
- **procload/dnd-mcp** — **no detectable license** (effectively all rights
  reserved). The idea of fuzzy rules lookup over the 5e API is referenced; code
  is not.
- **heffrey78/dnd-mcp** — no declared license. Encounter / CR-difficulty logic
  referenced only; reimplemented from the SRD.
- **Sstobo/Claude-Code-Game-Master** — CC-BY-NC-SA-4.0 (NonCommercial +
  ShareAlike), incompatible with WorldOS licensing. Architecture studied; not
  copied.
- **PinchOfData/claude-dungeon-master** — no declared license. DM persona/tone
  studied; not copied.

## Content policy

Published commercial **adventures** (e.g. WotC titles) are copyrighted and are
**never** redistributed in this repository. WorldOS ships original and
CC-licensed content, generates campaigns from SRD primitives, and supports
*private, local* import of adventures the user legally owns (kept under a
git-ignored directory and never committed).

## World seeds (`content/worlds/`)

World seeds are a distinct content layer with their own licensing (the root
WorldOS license does **not** override separate per-seed terms). Each seed ships a
`LICENSE.md`; the CI gate (`scripts/license_check.py`) requires it.

- **Original seeds** (e.g. *The Sundered Reach*) — original WorldOS content under
  **CC-BY-4.0**, built on SRD primitives. Clean-room; no third-party setting IP.
- **Universe seeds based on existing settings** (e.g. the *Unofficial Baldur's
  Gate 3+ Universe Seed*) — **FREE, unofficial Fan Content**, never sold:
  - Game rules: **CC-BY-4.0 SRD / D&D Open Game License**.
  - Setting names/lore/characters: **Wizards Fan Content Policy** —
    *"Unofficial Fan Content permitted under the Fan Content Policy. Not
    approved/endorsed by Wizards. Portions of the materials used are property of
    Wizards of the Coast. ©Wizards of the Coast LLC."* Baldur's Gate 3 elements are
    the property of **Larian Studios**, used as unofficial fan content.
  - These seeds are not official and not endorsed by any rights-holder.
- A `content/worlds/_private/` path stays git-ignored for unpublished seeds.
