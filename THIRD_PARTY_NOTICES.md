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

## Adapted source (permissive — code may be reused with attribution)

### study-flamingo/gamemaster-mcp
- **License:** MIT
- **Used for:** the campaign/party/character/combat state model is *adapted*
  from this project's schema. Reimplemented in `servers/engine/models.py`.

### procload/dnd-mcp
- **License:** MIT
- **Used for:** the 5e-API lookup + fuzzy/synonym-matching *pattern* informs
  `servers/rules`.

### Kokoro-82M / kokoro (PyPI) and mberg/kokoro-tts-mcp
- **License:** Apache-2.0
- **Used for:** the default local text-to-speech backend
  (`servers/voice/adapters/kokoro.py`). Integration pattern referenced from
  kokoro-tts-mcp.

## Learn-from-only (NOT copied — incompatible or unclear licensing)

These projects informed our design, but **no code is copied** from them:

- **Sstobo/Claude-Code-Game-Master** — CC-BY-NC-SA-4.0. The NonCommercial +
  ShareAlike terms are incompatible with ClawDnD's permissive MIT licensing, so
  we studied its architecture only and reimplemented clean.
- **heffrey78/dnd-mcp** — no declared license (all rights reserved). Its
  encounter / CR-difficulty logic is referenced as a reference only and
  reimplemented from the SRD.
- **PinchOfData/claude-dungeon-master** — no declared license. DM persona/tone
  studied; not copied.

## Content policy

Published commercial adventures (e.g. WotC titles) are copyrighted and are
**never** redistributed in this repository. ClawDnD ships original and
CC-licensed content, generates campaigns from SRD primitives, and supports
*private, local* import of adventures the user legally owns (kept under a
git-ignored directory and never committed).
