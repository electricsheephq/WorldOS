# ClawDnD — working guide (read before editing/running)

A post-Baldur's-Gate-3 D&D 5e living-world game shipped as a Claude Code plugin, with a
native macOS app. This file is auto-loaded when working in the repo — it exists so we never
again run the wrong UI or work from the wrong checkout (both have bitten us).

## Which checkout am I in?
- **Canonical working checkout: `/Users/lume/ClawDnD-val`** — it tracks GitHub
  `100yenadmin/ClawDnD` `main`. Edit, run, commit, and verify here.
- **`/Volumes/LEXAR/repos/ClawDnD-val` and `/Volumes/LEXAR/repos/ClawDnD` are DEPRECATED**
  (the pre-2026-05-28 location). Do not edit or run from them. They're kept only as
  fast-forwarded read-only mirrors and are guarded so an accidental run still serves local code.
- If `git remote -v` shows `100yenadmin/ClawDnD` but your `pwd` is under `/Volumes/LEXAR/…`,
  you're on the deprecated mirror — switch to `/Users/lume/ClawDnD-val`.
- The Claude Code **preview tool roots at the LEXAR path**, so don't trust `preview_start`
  blindly — run the viewer yourself (below) from the canonical checkout, or confirm the served
  code is current (root must 302→`/openworlds/`; `chrome.jsx` must have zero `traffic-lights`).

## The UI is OpenWorlds — NOT the root dashboard
- The real, current UI is the **OpenWorlds React SPA at `/openworlds/`**
  (`viewer/openworlds/*.jsx`, in-browser Babel, no build step), served by `viewer/server.py`.
  The native app `dist/ClawDnD.app` loads `/openworlds/`.
- **`http://127.0.0.1:<port>/` (the root) is the LEGACY pre-OpenWorlds dashboard** — it now
  redirects to `/openworlds/`. Never treat the root, `viewer/index.html`, or `dashboard.html`
  as the current UI. Always verify at `/openworlds/`.

## Run / verify the viewer
```sh
CLAWDND_STATE_DIR=<state-dir> CLAWDND_REPO_ROOT="$PWD" \
  python3 "$PWD/viewer/server.py" "" <port>      # argv: server.py [campaign] [port]
# Pass "" for the campaign to just set a port (a bare numeric arg is read as a CAMPAIGN id,
# not the port, and silently binds the default 8765). Then open /openworlds/ — not the root.
```
Headless capture: `qa/owshot.sh <screen-hash> <out.png> <port>` (fresh Chrome profile → no
stale cache). The viewer sends `Cache-Control: no-store` and version-stamps the index scripts,
but a long-lived browser profile can still hold an old copy — use a fresh profile when in doubt.

## Architecture invariants
- **The engine (`servers/engine/`) is the SOLE writer** of `snapshot.json`. The viewer, the
  native app, and the player facade only READ engine read-models and submit intent via
  `POST /move`. Never write play-state directly.
- Three MCP servers (`.mcp.json`): engine, rules, voice.

## Don't repeat these (hard-won)
- **Version-skew:** never `git commit` engine `.py` while a play/QA session is running. A
  long-lived DM engine subprocess running older `models.py` than the snapshot writer fails
  every tool call with pydantic `extra_forbidden`. Commit engine changes BEFORE starting a
  session. Viewer/.jsx, qa/, content/, docs/, skills/, macos/ are session-safe.
- **One checkout:** a stale second checkout silently serving old code is the #1 cause of
  "the fixes aren't there / wrong UI." Keep the LEXAR mirror fast-forwarded after every push.

## Testing characters
Use CANON BG NPCs from the 2076-record pool (e.g. `dal-lightspark`) — they have real ingested
portraits. NOT the 7 BG3 origin heroes (overpowered), and NOT custom PCs. A character with no
ingested face shows a neutral silhouette — never a class/race heraldic crest.

## Tests
Engine: `uv run --directory servers/engine python -m pytest tests/ -q` (single-process, ~15s,
1400+ tests). Viewer: `uv run --directory viewer pytest -q tests/`. Prefer GitHub CI for the
full sweep when the main disk is tight; engine single-process locally is light.
