# Third-Party Notices

ClawDnD's own source code is licensed under MIT (see `LICENSE`). This file
records third-party material that ClawDnD bundles, adapts, or depends on, and
the licensing decisions behind them.

## Bundled data

### System Reference Document 5.2 (SRD 5.2)
- **License:** Creative Commons Attribution 4.0 International (CC-BY-4.0)
- **Location:** `data/srd/`
- **Attribution:** see `data/srd/ATTRIBUTION.md` (required by the license)
- CC-BY-4.0 permits commercial use and is irrevocable. We treat the SRD as the
  canonical, shippable rules source.

## Runtime dependencies (permissive, installed from PyPI via uv)

ClawDnD's MCP servers depend on these packages:
- **mcp** (MIT) — the Model Context Protocol SDK / FastMCP.
- **pydantic** (MIT) — state-model validation.
- **rapidfuzz** (MIT) — fuzzy matching for rules lookups.
- **httpx** (BSD-3-Clause) — HTTP client for the dnd5eapi.co fallback.
- **kokoro** (Apache-2.0) — local text-to-speech (added with the voice backend).

## Structural references — learn-from only (NO code copied)

These projects informed ClawDnD's design. After verifying their licensing
(2026-05-22), we copy **no code** from any of them; ClawDnD's engine, rules, and
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
  ShareAlike), incompatible with ClawDnD's MIT licensing. Architecture studied;
  not copied.
- **PinchOfData/claude-dungeon-master** — no declared license. DM persona/tone
  studied; not copied.

## Content policy

Published commercial adventures (e.g. WotC titles) are copyrighted and are
**never** redistributed in this repository. ClawDnD ships original and
CC-licensed content, generates campaigns from SRD primitives, and supports
*private, local* import of adventures the user legally owns (kept under a
git-ignored directory and never committed).
