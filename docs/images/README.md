# Public Image Policy

Images in this folder are safe for the public repo.

## What Can Be Committed

- Original WorldOS screenshots generated from public/demo content.
- Screenshots with private art disabled or replaced by public placeholders.
- App icons and interface crops that contain no private paths, credentials, private art, or raw evidence.
- Publicly licensed third-party icons when attribution is preserved.

## What Must Not Be Committed

- Private art.
- Screenshots from private worlds, private evidence bundles, or local operator runs.
- Images containing credentials, private paths, VM details, API keys, or user-owned copyrighted content.
- Ignored UI-audit screenshots that are documented as private-art derivatives.

## Current Images

The `openworlds-*-public.png` images were generated from a deterministic scripted OpenWorlds smoke with private art disabled/replaced by placeholders. They are intended for README/onboarding use, not QA release evidence.

## Regenerating Public Screenshots

Use an original or public demo world, set the art root to a checkout without private art, and save screenshots outside the repo first. Inspect them before copying selected files into `docs/images/`.

Do not copy raw evidence bundles into this directory.
