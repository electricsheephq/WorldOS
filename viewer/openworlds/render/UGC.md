# WorldOS UGC render-profiles (M3 · #453 ownership · #442 authoring v0)

How a user (or the AI build-loop) persists, owns, and ships a graphical WorldOS game — built as
an **additive, server-owned, versioned store**, with the engine's invariants intact.

## The load-bearing design call

**A render-profile is PRESENTATION, not game STATE.** The engine is the sole writer of game
state (`snapshot.json`); a render-profile only *joins* to that state by id (`engine_location_id`
/ `engine_actor_id`). So persisting UGC profiles:
- needs **no change to `servers/engine/`** (collision-safe with the engine team's work), and
- does **not** weaken the engine's sole-writership — profiles are a *separate* artifact class.

They are **server-owned**: stored under the state dir, **versioned append-only** (an edit is a new
version — ownership + history are never lost), and mutated **only via a constrained, validated
save-intent** — exactly mirroring the `/move` pattern. A client never writes the store directly;
it submits a profile and the server **gates it against the frozen contract** before persisting.

## Store layout
```
<state_dir>/ugc/render-profiles/<owner>/<game_id>/v<N>.json   (+ latest.json)
```
- `owner` — per-user namespace (`"local"` for the single-user v0; ready for multi-user).
- `game_id` — the profile's slug. Both are **slugified + traversal-proof** (no `/`, no `..`).
- `v<N>` — 1-based, monotonic, append-only.

## API (additive viewer routes)
| Route | Method | Role |
|-------|--------|------|
| `/ugc/profile` | `POST` | **save-intent**: `{profile, owner?}` → validate + gate → persist a new version, or reject with the failing gates + human-gate queue. |
| `/ugc/profiles` | `GET` | list stored games `{owner, game_id, title, scene_kind, latest_version, versions}` (read-only). |
| `/ugc/profile?game_id=&owner=&version=` | `GET` | load a stored profile (latest, or an exact version); `404` if absent. |

A stored game renders with **zero new renderer code**: point a page's
`window.WORLDOS_PROFILE_URL` at `/ugc/profile?game_id=<id>&owner=<owner>` and load the generic
renderer for its `scene_kind`. (Proven: generate → save → load → render gate exit 0.)

## Authoring v0 (#442)
The build-loop is the authoring front door: a seed (the user's prompt + placed locations/actors)
→ `generate_profile` → `gate` → **`POST /ugc/profile`**. "AI fills a scene from a prompt" = the
seed; "user places tiles/actors" = the seed's `locations`/`actors`. The result is persisted as
the user's owned, versioned game. Richer in-browser authoring (drag-place, live edit) builds on
this store.

## Ownership + MIT redistribution story (#453)
- **The runtime is MIT-clean to ship.** The renderers are **vendored Phaser (MIT)** + our own
  MIT glue (`renderer-*.js`, `surface-client.js`). A user can redistribute a game built on them.
- **The user owns their profile + procedural/generated art.** A generated render-profile and the
  procedural placeholder art are the creator's.
- **The first-party BG catalog is NEVER shippable in UGC.** It is internal/first-party reference
  only. The build-loop's human-gate **`ai-disclosure-and-rights`** item flags this on every
  profile, and the store persists only profiles that passed the gate — so the catalog can't leak
  into a shippable UGC game.
- **AI-disclosure travels with the profile** (`core.ai_disclosure`), feeding the EU
  machine-readable obligation + Steam's AI survey downstream (#454).

## Deferred (owner decisions, per the roadmap triggers)
- **#454 shippable-UGC asset model** (self-hosted vs paid image API) — owner cost/dependency
  decision; **deferred** (owner, 2026-06-02): ship the build-loop + store with first-party gen
  only; the shippable UGC asset model is decided when there's real creator demand. The disclosure
  + rights *metadata* path is already wired.
- Multi-user auth / quotas / moderation — future, once the single-user `owner="local"` v0 lands.

Implemented by `viewer/ugc_store.py` (stdlib, no engine import) + the three routes in
`viewer/server.py`. Tested by `viewer/tests/test_ugc_store.py` (store unit + traversal-safety +
HTTP round-trip).
