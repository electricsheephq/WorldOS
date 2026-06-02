# Graphical move-intent vocabulary (M0)

> **Status:** M0 — **IMPLEMENTED.** This was the spec for #429 (`_MOVE_KINDS` += travel /
> inspect / examine / move_to_zone) and #430 (document + reject-unknown test). The code now
> lives in `viewer/server.py:sanitize_move` (the graphical intents are accepted, carried by
> `target`; existing kinds + invariants unchanged) and is covered by
> `viewer/tests/test_move_intents.py`. The companion #432 (derived combat-token x/y flagged
> `positionAuthority:"derived"`) is also landed in `_combat_tokens`. Verified live: each new
> intent POSTs 200 to `/move` and lands in the moves file the DM reads; unknown kinds +
> missing-target are rejected.

## Why this must freeze in M0 (before any tier ships)

A graphical renderer's only write is an **intent** to `POST /move`. The engine's facade
(`viewer/server.py:sanitize_move`) validates each move against a **closed allowlist**
`_MOVE_KINDS` and **drops unknown fields** (a deliberate anti-injection design). So a new
kind is additive at the facade but **semantic for every consumer** — the renderer, the DM
skill, and the AI build-loop glue all encode it.

If M1 ships click-to-travel as free text (`{kind:"do", text:"go to the harbor"}`) and M2
later introduces a structured `{kind:"travel", target:"loc-harbor"}`, then **every M1 game +
all generated glue emits the old shape** and the change becomes breaking. Freezing the
vocabulary now makes it additive forever.

## The contract today (verified, `viewer/server.py:84-85`)

```python
_MOVE_KINDS  = {"say", "do", "check", "save", "combat", "attack", "cast", "use_item", "clarify"}
_MOVE_FIELDS = ("text", "name", "skill", "target", "weapon", "dc")
_MOVE_MAXLEN = 2000
```

`sanitize_move` forces `role="player"` (no impersonating dm/system), requires a known `kind`,
length-caps text, and drops unknown keys. This stays exactly as-is for every existing kind.

## The additive graphical intents (M0 freeze)

| Kind | New? | Fields | Meaning | Reads back from |
|------|------|--------|---------|-----------------|
| `say` | existing | `text` | In-fiction speech | `/chat`, `/events` |
| `do` | existing | `text` | Free-text action (the universal fallback) | `/chat`, `/events` |
| `check` / `save` | existing | `skill`/`name`, `dc` | Ability check / saving throw | `/combat-surface`, `/events` |
| `combat` / `attack` / `cast` / `use_item` | existing | `name`, `target`, `weapon` | Combat actions | `/combat-surface`, `/events` |
| `clarify` | existing | `text` | Ask the DM a question (never a world-assertion) | `/chat` |
| **`travel`** | **new** | `target` (= `engine_location_id`) | Move the party to a known/adjacent location | `/atlas-surface` (current_location flips) |
| **`inspect`** (alias `examine`) | **new** | `target` (location / actor / object id) | Look closely; request detail/description | `/events`, `/chat` |
| **`move_to_zone`** | **new** | `target` (= zone name) | Reposition within the current scene's named zones | `/combat-surface` (token zone updates) |

### Field reuse
All three new kinds reuse the existing `target` field (already in `_MOVE_FIELDS`) — so the
only change is to `_MOVE_KINDS` (add `travel`, `inspect`, `examine`, `move_to_zone`). No new
field is required, which keeps the anti-injection field-allowlist untouched.

### Gesture → intent mapping (how renderers emit these)
- **Click a travel chip / map exit** → `{kind:"travel", target:"<location_id>"}`
- **Click an actor / scenery / item** → `{kind:"inspect", target:"<id>"}`
- **Click a walkmask point / drag a token to a zone band** → `{kind:"move_to_zone", target:"<zone name>"}`
- **Target-pick + ability bar (combat)** → existing `attack`/`cast`/`use_item` with `target`

## Acceptance criteria (for the #429/#430 implementation PR)

1. `_MOVE_KINDS` gains `travel`, `inspect`, `examine`, `move_to_zone`; existing kinds + the
   `do` free-text fallback are unchanged.
2. `sanitize_move` accepts the new kinds with a `target`, still forces `role=player`, still
   drops unknown fields, still length-caps.
3. **Reject-unknown-kind test:** an unknown kind (e.g. `narrate`, `teleport`) is rejected
   with `unknown move kind`.
4. **Round-trip test:** each new kind survives `sanitize_move` with its `target` intact.
5. **Cross-component:** the viewer allowlist, the DM skill's intent interpretation, and the
   engine-facade allowlist (`servers/engine/player_server.py`) all agree — landed in **one**
   PR (not viewer-only), because they are one contract.
6. The DM skill learns to interpret the three new kinds (travel → move the party; inspect →
   narrate detail; move_to_zone → reposition in the scene).

## Out of scope
- Drag-to-zone *gestures* and ability-bar *verbs* beyond the kinds above (Shove/Dash/Hide as
  first-class buttons) — Branch B; ride in `do` until then.
- Any kind that asserts world state (only the DM/engine writes the world; players send intents).
