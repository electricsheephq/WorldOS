# OpenWorlds Design Asset Policy

The OpenWorlds design bundle is a local product-design reference for the native macOS app and dashboard polish. It may guide layout, interaction structure, and abstract visual tokens, but it is not a source of production game assets or canonical ClawDnD content.

## Allowed

- Reimplement abstract layout ideas such as a navigation rail, parchment panels, brass controls, campaign launcher, and CRPG-style status surfaces.
- Translate color, spacing, typography, and component intent into ClawDnD-owned code.
- Use generated or original ClawDnD assets in later PRs when their source/license is documented.
- Reference local scratch artifacts under `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/` in issue and PR bodies.

## Not Allowed

- Commit `OpenWorlds.zip`, extracted screenshots, pasted reference images, or binary mockup assets from the design bundle.
- Copy Owlcat, Baldur's Gate, D&D-private, or other proprietary UI art into the repo.
- Promote prototype/test copy from the bundle into canonical campaign, lore, world seed, item, NPC, or companion content.
- Let visual UI code write `play-state`, `qa/state`, engine snapshots, or provider state directly.

## PR Checklist

- State whether any assets were copied. The expected answer for OpenWorlds visual PRs is "no third-party/reference assets copied."
- Keep visual/design implementation scoped to `macos/`, `viewer/`, `docs/`, or build scripts unless the issue explicitly needs engine read models.
- Run `python3 scripts/license_check.py` for PRs that touch content, data, licensing docs, or bundled assets.
- Confirm `git status --short` does not include extracted design artifacts, runtime state, `.build/`, `dist/`, transcripts, or private content.
