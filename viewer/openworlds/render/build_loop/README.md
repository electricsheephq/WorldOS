# WorldOS build_loop/ — the gated AI build-loop (M3 · #449–#452)

The unattended pipeline that turns a **game seed** into a **running graphical WorldOS game** —
without a human writing a render-profile by hand and without ever letting the AI touch the things
only a human should own. It is the codegen layer on top of the M0 contract + the M1/M2 generic
renderers.

```
seed.json ──▶ generate_profile.py ──▶ gate.py ──▶ emit_glue.py ──▶ <game>.index.html
   (#449/#450)                       (#452)        (#451)
              run_loop.py orchestrates all three, with the gate ENFORCED.
```

## The two hard boundaries (why an unattended loop is safe)

1. **The loop MUST NOT mutate the contract.** Every stage reads the frozen
   `docs/roadmap/contracts/render-profile.schema.json` *read-only*. A seed field with no home in
   the contract is collected as `_unmapped` and **routed to the human-gate queue** — never
   invented into the schema. A proposed contract change is a human decision.
2. **Taste / story / rights are human-gated.** The harness gates the **objective** properties
   (schema-valid / contract-invariants / art-present / no-overlap / renders-clean /
   blind-playtester). It routes the **subjective** ones (art-taste sign-off, story/lore approval,
   AI-disclosure + asset-rights compliance) to people. ~70–80% of scaffolding is unattended; the
   rest is surfaced, not auto-passed.

## Files

| File | Issue | Role |
|------|-------|------|
| `generate_profile.py` | #449, #450 | seed → schema-valid render-profile; resolves `art.scope_key` (Img-scope → `/image`, first-party imagegen + BG catalog), stamps `ai_disclosure`, collects unmapped fields for the human gate. Defaultable → partial seeds are valid. |
| `gate.py` | #452 | the gate: schema-valid / contract-invariants / art-present / no-overlap (required) + renders-clean / blind-playtester (optional hooks) + the human-gate queue. Loop is REJECTED on any required failure. |
| `emit_glue.py` | #451 | emits a per-game thin-client entry page that injects the profile (`window.WORLDOS_PROFILE`) and loads the **generic** vendored-Phaser renderer for the scene_kind. Zero renderer forks — every game inherits renderer improvements. |
| `run_loop.py` | #449–#452 | orchestrator: generate → gate → emit, gate enforced; writes `<game>.profile.json` + `<game>.index.html` on accept, always writes `<game>.human-gate.json`. |
| `example-seed.json` | — | a sample backdrop-tier seed ("The Embergloom Pact"). |

## Run it

```bash
cd viewer/openworlds/render/build_loop
# one-shot: seed -> profile -> gate -> glue
python3 run_loop.py example-seed.json --outdir /tmp/wos-games --date 2026-06-02
# or step by step:
python3 generate_profile.py example-seed.json --out /tmp/p.json --date 2026-06-02
python3 gate.py /tmp/p.json                 # exit 0 iff required gates pass
python3 emit_glue.py /tmp/p.json --out /tmp/game.html
```

`renders-clean` is gated separately by `qa/render_gate_probe.js` (needs `qa/playwright`); the
generated `<game>.index.html` serves at any path under `/openworlds/render/` and is a valid probe
target. `blind_playtester` (#441) is a reserved hook for `qa/ui_playtest.sh` persona traversal —
reported as *deferred* until wired, never silently passed.

## What's NOT here (deferred — stop-and-ask / engine-state · #453, #454-shippable, #455)

These hit the roadmap's stop-and-ask triggers and are intentionally **out of this net-new,
additive milestone slice**:
- **#453 UGC per-user ownership (engine-owned persistence)** — persisting user games as
  engine-owned state touches the **sole-writer engine**; designed as an additive surface but
  gated on owner confirmation (engine-state change).
- **#454 shippable-UGC asset model** — self-hosted vs paid image API for *user-shippable* games
  is an owner **cost/dependency decision**. The disclosure + rights *metadata* path is built (it
  flows through `ai_disclosure` + the human-gate `ai-disclosure-and-rights` item); the model
  choice is the owner's.
- **#455 websocket/SSE transport** — adds an engine push channel (additive, polling stays
  fallback) but edits shared engine files → sequenced Lane-B against the main agent.

Stdlib-only, deterministic (no clock/RNG — pass `--date`), no network. Tested by
`viewer/tests/test_build_loop.py`.
