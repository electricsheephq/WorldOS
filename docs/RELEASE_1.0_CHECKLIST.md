# WorldOS 1.0.0 — Release Checklist

> Historical checklist. Do not execute this as the current release path. Current release truth requires
> the hardened non-partial RRI in `qa/release_readiness.py`, with same-SHA built-app proof and complete
> persona evidence recorded in `qa/SCORECARD.md`. Start from `WorldOS-OPERATING-GOAL.md`,
> `WorldOS-RUNBOOK.md`, and `qa/QA_TOOLS.md`.

**Scope:** local/personal 1.0 build. The bundled `baldurs-gate` world ships as-is for
personal use (Wizards Fan Content Policy). Public distribution and notarization are
deferred to 1.0.1 (see `macos/WorldOSApp/RELEASE_CHECKLIST.md` for the signing state).

Do NOT execute steps here until the pre-release sanity checks below are green.

---

## Pre-release sanity checks (run first)

- [ ] **Engine tests green in CI.** Confirm the latest commit on `main` has a passing
  GitHub Actions run. Check with:
  ```bash
  gh run list --branch main --limit 5
  gh run view <run-id>
  ```
  The engine pytest suite currently collects ~353 tests across `servers/engine/tests/`,
  `servers/rules/tests/`, `servers/voice/tests/`, and `qa/`.

- [ ] **License check passes locally.**
  ```bash
  python3 scripts/license_check.py
  ```
  Must print `license check passed`. All three world seeds (`sundered-reach`,
  `tidal-commonwealth`, `baldurs-gate`) must have a `LICENSE.md` beside their
  `world.json`.

- [ ] **Played session verified.** Run one living-world session end-to-end and confirm:
  - The DM opens a world, introduces a companion, and delivers an opening scene.
  - At least one combat encounter resolves (attack roll → damage → HP updated).
  - `/save` writes a `state/` checkpoint; `/session-start` reloads it with a recap.
  - Voice plays (or `/voice-toggle off` text fallback works).
  - Dashboard play surface works: `scripts/play.sh sundered-reach` opens a browser and
    the action palette accepts a Say / Do move.

- [ ] **Native app builds.** From a clean checkout:
  ```bash
  script/build_and_run.sh
  ```
  Confirm `WorldOS.app` launches in `dist/` and the OpenWorlds screens load.

---

## Step 1 — bump the plugin version to 1.0.0

File: `.claude-plugin/plugin.json`

Change `"version": "0.2.0"` to `"version": "1.0.0"`.

```bash
# Edit manually, then verify:
python3 -c "import json; d=json.load(open('.claude-plugin/plugin.json')); assert d['version']=='1.0.0'"
```

Also update `.claude-plugin/marketplace.json` if it carries a version field.

---

## Step 2 — commit the version bump

```bash
cd /path/to/WorldOS          # your local checkout (not a QA worktree)
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json
git commit -m "chore: bump version to 1.0.0"
git push origin main
```

Wait for CI to go green on that commit before tagging.

---

## Step 3 — tag v1.0.0

```bash
git tag -a v1.0.0 -m "WorldOS 1.0.0 — local/personal release"
git push origin v1.0.0
```

---

## Step 4 — create the GitHub release

Extract the 1.0.0 section from `CHANGELOG.md` into a temp file for the release notes,
then publish:

```bash
gh release create v1.0.0 \
  --title "WorldOS 1.0.0 — Living-World Engine" \
  --notes-file <(awk '/^\[1\.0\.0\]/{found=1} found && /^\[0\.[0-9]/{exit} found{print}' CHANGELOG.md) \
  --draft
```

Review the draft on GitHub (`gh release view v1.0.0 --web`), then publish:

```bash
gh release edit v1.0.0 --draft=false
```

No binary artifacts are attached for this local build. If you want to ship a zip of
`dist/WorldOS.app` for personal archival:

```bash
script/build_and_run.sh   # builds dist/WorldOS.app
cd dist && zip -r WorldOS-1.0.0-macos.zip WorldOS.app
gh release upload v1.0.0 WorldOS-1.0.0-macos.zip
```

---

## Step 5 — build-from-source app (final smoke)

```bash
script/build_and_run.sh            # default: build + open
script/build_and_run.sh --verify   # build + open + confirm process alive
script/build_and_run.sh --release-check  # build + codesign audit
```

The script lives at `script/build_and_run.sh` (Swift build → bundle → ad-hoc sign →
open). Requires macOS 13+, Xcode Command Line Tools (`xcode-select --install`).

Signing state at 1.0: ad-hoc only. Gatekeeper will warn on first launch — right-click →
Open to bypass. Full Developer ID signing + notarization is a 1.0.1 item (see
`macos/WorldOSApp/RELEASE_CHECKLIST.md`).

---

## 1.0 scope decisions (record for 1.0.1 planning)

| Item | 1.0 disposition | Deferred to |
|---|---|---|
| `baldurs-gate` world seed | **Ships** for personal use (Fan Content Policy) | — |
| Public distribution / App Store | Not in scope | 1.0.1 |
| Developer ID signing | Not in scope | 1.0.1 |
| Notarization | Not in scope | 1.0.1 |
| ElevenLabs TTS backend | Stub present; not default | Later |
| STT / voice input | Seam present; no live backend | Later |
| Tier-2 OpenClaw companion fork | Seam present; not wired | Tier-2 milestone |

---

## Reference paths

| Artifact | Path |
|---|---|
| Plugin manifest | `.claude-plugin/plugin.json` |
| Marketplace metadata | `.claude-plugin/marketplace.json` |
| MCP server config | `.mcp.json` |
| Engine server | `servers/engine/server.py` |
| Build script (macOS app) | `script/build_and_run.sh` |
| Play dashboard script | `scripts/play.sh` |
| License check | `scripts/license_check.py` |
| Changelog | `CHANGELOG.md` |
| Native app package | `macos/WorldOSApp/Package.swift` |
| World seeds | `content/worlds/{sundered-reach,tidal-commonwealth,baldurs-gate}/` |
| App source | `macos/WorldOSApp/Sources/WorldOSApp/` |
