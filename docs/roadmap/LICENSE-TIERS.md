# License Tiers — Template-Pack License Registry

**Epic #644 (open-IP template packs). Status: contract / registry doc — DEFINES what the
eventual code enforces. No enforcement code lives here.**

A **template pack** wraps an existing `content/worlds/<id>/` tree (its `world.json` seed +
ingest manifests + tuned config + playtested starter content) into a *redistributable,
attribution-clean* unit. The thing that decides whether a pack is shippable — and what
obligations ride along when someone forks it — is its **license tier**.

This doc is the **canonical, ordered registry** of those tiers, the **rollup rule** that
computes a pack's effective tier from its sources, the **two named traps** that have
sunk open-IP game content before, and the **`pack.json` schema** that carries the tier as
a machine-checkable field.

> **Enforcement seam (do NOT edit from this doc):** the future tier validator extends
> [`scripts/license_check.py`](../../scripts/license_check.py) — the existing CI gate that
> already (a) blocks `content/worlds/_private/` and other private prefixes from being
> committed, (b) requires every committed `world.json` to carry a sibling `LICENSE.md`, and
> (c) requires per-source `license` + `attribution` on every ingested record/wiki page.
> `pack.json` tier-consistency checking is the next layer on that same gate. This doc is
> the contract; `license_check.py` is where the contract becomes CI-enforced.

---

## 1. The ordered tiers (most-permissive → most-restrictive)

The order is a **total order of restrictiveness**. "More restrictive" always wins in the
rollup (§2). A pack may ship freely only at the top of this ladder; the bottom two rungs
are **ship-blocked** for the public, redistributable product lane.

| Rank | Tier (`license_tier`) | What it means | Ships in the public pack lane? |
|---|---|---|---|
| 1 | **`PD`** | Public domain. No attribution *required*; no downstream obligation. (Greek/Norse myth, Malory's Arthur, Stoker's *Dracula*, US-PD Sherlock Holmes.) | ✅ Yes. The clean ceiling. |
| 2 | **`CC-BY`** | Creative Commons Attribution. Free to redistribute and use in games (incl. commercial) **as long as attribution is carried**. The **D&D SRD 5.1/5.2 is CC-BY-4.0** and every pack inherits it as its rules spine. | ✅ Yes, with attribution carried in `license_rollup`. |
| 3 | **`CC-BY-SA`** | Attribution **+ ShareAlike**. ⚠️ **Share-alike INFECTS the derived pack and every downstream fork** — the whole derived work (and forks of it) must be re-licensed CC-BY-SA. (SCP Foundation, Fandom/wiki prose, Wikipedia prose.) See **Trap 1**. | ⚠️ Yes *only if* the pack and its forks accept the BY-SA obligation; refuse if any downstream consumer must stay closed. |
| 4 | **`COMMUNITY-PROGRAM`** | A publisher "fan content" / compatibility program. ⚠️ These very often **EXCLUDE video games** from the grant (or reserve the setting/trademark even when they license the rules). (WotC Fan Content Policy; Paizo Pathfinder Compatibility License / ORC setting-reservation.) See **Trap 2**. | ❌ **Ship-blocked.** Stays `_private`/opt-in; never enters the redistributable product lane. |
| 5 | **`ARR`** | All rights reserved. No grant. | ❌ **Ship-blocked.** Never committed in the public lane; lives only under a gitignored `_private/` prefix. |

**Canonical comparison string** (this is the rollup order — memorize it):

```
PD  >  CC-BY  >  CC-BY-SA  >  COMMUNITY-PROGRAM  >  ARR
       (free)   (share-alike   (often EXCLUDES        (no grant)
                 INFECTS         video games)
                 derived)
```

`>` reads "is **more permissive than**." The rollup picks the **rightmost** (most
restrictive) tier present across all sources (§2).

### Where today's worlds sit

The repo already proves the top two rungs in practice — **2 of the 3 existing worlds are
clean at the top of this ladder**:

| World | Effective tier | Why |
|---|---|---|
| `content/worlds/sundered-reach` | **`CC-BY`** | Its `LICENSE.md` declares **"Original WorldOS world content — free and open"**; clean-room original prose under CC-BY-4.0 + SRD rules (CC-BY-4.0). No third-party setting IP. |
| `content/worlds/tidal-commonwealth` | **`CC-BY`** | Same — **"Original WorldOS — free and open"**, clean-room CC-BY-4.0 + SRD. |
| `content/worlds/baldurs-gate` | **`COMMUNITY-PROGRAM`** | Forgotten Realms / BG3 setting used under the **WotC Fan Content Policy** (+ Larian for BG3 elements). Free, non-commercial, *unofficial* fan content — **ship-blocked** for the public product lane (Trap 2). Its ingested `lore/wiki/` pages are additionally **CC-BY-SA** (Trap 1), so even its *ingest layer* carries a share-alike obligation. |

So the **clean-tier reference already exists** on disk: a new open-IP pack (e.g. a Greek-myth
`PD` pack) proves the *ingested-public-domain* path, not the *first* clean pack. The
Baldur's-Gate seed is the reference *structure* but is itself the worst-tier example —
which is exactly why #644 needs a machine-checkable tier, not a vibe.

---

## 2. The rollup rule (effective tier = most-restrictive source, inherited through forks)

A pack is rarely a single source. It is typically: **clean-room prose** (`CC-BY`) **+
SRD rules** (`CC-BY`) **+ ingested wiki lore** (`CC-BY-SA`) **+ optional setting program**
(`COMMUNITY-PROGRAM`). The pack's **effective `license_tier` is the single most-restrictive
tier among all of its `license_rollup` sources.**

```
effective_tier(pack) = max_restrictive( source.license for source in pack.license_rollup )
```

Two properties make this load-bearing:

1. **Most-restrictive-source wins (monotone downgrade).** Adding *one* CC-BY-SA wiki to an
   otherwise-PD pack makes the **whole pack** `CC-BY-SA`. Adding *one* community-program
   source (a reserved setting, a fan-content-only grant) makes the whole pack
   `COMMUNITY-PROGRAM` and **ship-blocks it**. You can never roll *up* — a single bad source
   poisons the whole pack's tier. This is why the tier is computed, not declared.

2. **Inheritance through forks.** A fork **inherits its parent's effective tier** and can
   only ever move it *down* (more restrictive), never up. If you fork a `PD` pack and add a
   Fandom-ingested region, your fork is `CC-BY-SA`. If a downstream consumer forks *your*
   fork, they inherit `CC-BY-SA` too — the obligation rides the whole derivation chain.
   `pack.json` records this with `license_inherits: true`; a fork that drops the parent's
   obligations is a contract violation the validator must reject.

> **Why "most-restrictive" and not "declared":** a pack author declaring `license_tier: "PD"`
> while ingesting a CC-BY-SA wiki is the single most common way open-IP content ships with a
> latent legal landmine. The rollup makes the tier a *function of the sources*, so the
> validator can recompute it from `license_rollup` and **fail the build when the declared
> tier is more permissive than the computed one.**

---

## 3. The two named traps

These are the two failure modes the rubric and the validator **must** encode as hard,
distinct boolean flags on `pack.json` — they are not the same trap and are caught by
different checks.

### Trap 1 — Share-alike INFECTS derived content (`share_alike: true`)

**CC-BY-SA is viral.** When a pack ingests CC-BY-SA material, the *derived pack* — and every
downstream *fork* of it — **must remain CC-BY-SA**. You cannot ingest a CC-BY-SA wiki and
then ship the result under a more permissive (or closed) license. The obligation propagates
through the whole derivation chain (§2).

- **Canonical example: SCP Foundation = CC-BY-SA 3.0.** The highest-fandom-pull open horror
  setting, but **share-alike-infectious**: an SCP-derived pack and all its forks must stay
  CC-BY-SA, *and* specific assets (e.g. the SCP-173 image) are separately ©, *and* the "SCP"
  mark is litigated. A pack that ingests SCP **must** set `share_alike: true`.
- **Same clade:** Fandom wiki prose, Wikipedia prose (both CC-BY-SA 4.0). Note the existing
  `baldurs-gate` world already hits this through its `lore/wiki/` (Forgotten Realms Fandom,
  CC-BY-SA) — its *ingest layer* is share-alike even though the BG block above is about the
  *setting* trap.
- **Validator behavior (future, in `license_check.py`):** if any `license_rollup` source is
  `CC-BY-SA`, the rollup tier is **≤ `CC-BY-SA`** and `share_alike` **must** be `true`. A
  pack that ingests share-alike content but declares `share_alike: false` (or a more
  permissive `license_tier`) **fails CI**. A `pack export` of a `share_alike: true` pack must
  refuse to drop the downstream re-licensing notice.

### Trap 2 — Community-program EXCLUDES video games (`excludes_video_games: true`)

A publisher "fan content" or compatibility program grants *some* rights but commonly
**carves out video games** (or reserves the setting/trademark even when the rules are
licensed). Content under such a program is **ship-blocked** from the public, redistributable
product lane — it is a *personal-seed* tier, not a *pack* tier.

- **Canonical example A: Pathfinder — ORC + Compatibility License.** The ORC license covers
  the *rules*, but **Golarion setting lore, deities, and places are RESERVED, not licensed**,
  and the "Pathfinder"-compatibility program / trademark **excludes video games for most
  publishers**. You may pack the *rules*, never the *setting*; a Golarion pack is ship-blocked.
- **Canonical example B: WotC Fan Content Policy.** This is what `content/worlds/baldurs-gate`
  runs under — *free, unofficial, non-commercial fan content, never sold.* Fan Content is
  **not** a redistributable-product grant; it does not license a shippable video-game product.
  So `baldurs-gate` is `COMMUNITY-PROGRAM`, `excludes_video_games: true`, **ship-blocked**.
- **Validator behavior (future, in `license_check.py`):** any `license_rollup` source flagged
  community-program forces the rollup tier to `COMMUNITY-PROGRAM`, sets
  `excludes_video_games: true`, and the pack is **blocked from the shippable product lane**
  (it may only live `_private`/opt-in, where `license_check.py`'s existing FORBIDDEN-prefix
  rule already keeps it out of the committed public tree).

> **The two traps are orthogonal.** A pack can be `share_alike: true` **and**
> `excludes_video_games: true` (e.g. a fan-content world whose lore is ingested from a
> CC-BY-SA wiki — exactly `baldurs-gate`). They are separate flags, separate checks, separate
> failure messages.

---

## 4. The `pack.json` schema

`pack.json` sits **beside** `world.json` in `content/worlds/<id>/`. It is the manifest that
upgrades a world *seed* into a redistributable *pack*: it points at the ingest manifests,
carries the tuned config, names the playtested starter content, and — load-bearing — carries
the **`license_tier`** plus the source-by-source `license_rollup` that the tier is computed
from.

```jsonc
{
  "pack_format": 1,
  "id": "greek-myth",                 // == folder name; what `pack install`/`start_world` take
  "world_ref": "world.json",          // the existing seed (schema_version 1)
  "title": "Aegis of Olympus",
  "version": "1.0.0",                 // semver; forks bump and inherit the tier (§2)

  // ── License (the load-bearing block) ──────────────────────────────────────
  "license_tier": "PD",              // PD | CC-BY | CC-BY-SA | COMMUNITY-PROGRAM | ARR (§1)
                                      // MUST equal the rollup-computed tier (§2) or CI fails
  "license_inherits": true,           // forks inherit this tier and may only move it DOWN
  "share_alike": false,               // Trap 1: true if any source is CC-BY-SA
  "excludes_video_games": false,      // Trap 2: true if any source is a game-excluding program
  "license_rollup": [                 // EVERY source + its license + attribution string.
                                      // The validator recomputes license_tier from this list.
    { "source": "Theoi / Wikipedia (myth facts)", "license": "PD",       "attribution": "..." },
    { "source": "SRD 5.2",                          "license": "CC-BY-4.0","attribution": "data/srd/ATTRIBUTION.md" }
  ],

  // ── Ingest manifest (reuses tools/ingest/manifest*.json verbatim) ─────────
  "ingest": {
    "lore":   "ingest/manifest.json",          // wiki categories/titles -> lore/wiki/*.md
    "images": "ingest/manifest_images.json",   // -> _private/<id>/images/ + .provenance.json (never committed)
    "ruleset": "SRD-5.2"                        // rules spine; attribution carried in license_rollup
  },

  // ── Tuned config (pack-specific overrides of engine defaults) ─────────────
  "tuning": {
    "tone_lens": "...",
    "safety_profile": "myth-violence-curated",
    "verb_weights": { "explore": 1.0, "talk": 1.0, "fight": 0.8, "check": 1.0, "level": 1.0 }
  },

  // ── Playtested starter content (the "actually playable" gate) ─────────────
  "starter_content": {
    "origins": ["origins/*.json"],
    "starting_options": [],
    "rri_proof": "qa/packs/greek-myth/rri.json"  // a PASSING RRI run is the install gate
  }
}
```

### Field contract (what the future validator checks)

| Field | Rule the validator enforces |
|---|---|
| `pack_format` | Currently `1`. Reserved for forward-compat. |
| `id` | Must equal the folder name (mirrors the `world.json` `id` rule). |
| `world_ref` | Must resolve to an existing `world.json` (`schema_version 1`) in the same folder. |
| `license_tier` | Must equal the tier **recomputed** from `license_rollup` (§2). Declared-more-permissive-than-computed ⇒ **CI fails.** |
| `license_inherits` | If `true`, a fork's tier is checked against the parent's (fork may only move down). |
| `share_alike` | Must be `true` iff any `license_rollup` source is `CC-BY-SA` (**Trap 1**). |
| `excludes_video_games` | Must be `true` iff any source is a game-excluding program (**Trap 2**); such a pack is ship-blocked. |
| `license_rollup` | Non-empty; every entry needs `source` + `license` + `attribution`. This mirrors the existing per-record `license`/`attribution` rule already in `license_check.py`. |
| `ingest.*` | Manifest paths must resolve; image manifests must target `_private/` (the existing FORBIDDEN-prefix rule keeps committed images out). |
| `starter_content.rri_proof` | Must point at a **passing** RRI run — a "template" with no passing playtest is not an installable pack. |

### How this layers on the existing gate (the seam, restated)

`scripts/license_check.py` today enforces: required top-level license files, no
`_private/`-prefix leaks, `world.json`⇒`LICENSE.md` pairing, and per-source
`license`+`attribution` on ingested records/wiki pages. The `pack.json` tier validator is
**the next conditional block on that same gate** — *"if a `pack.json` exists beside a
`world.json`, additionally verify the tier rollup, the two trap flags, fork inheritance, and
the RRI proof."* Nothing in this doc changes the existing checks; it specifies the contract
the new block implements. **Implement it by extending `scripts/license_check.py`, not by
forking a parallel checker.**

---

## 5. Quick reference

- **Order:** `PD > CC-BY > CC-BY-SA > COMMUNITY-PROGRAM > ARR` (left = ships freely; right = ship-blocked).
- **Rollup:** effective tier = most-restrictive source; inherited (down-only) through forks.
- **Trap 1 (`share_alike`):** CC-BY-SA infects the derived pack + all forks. Example: **SCP = CC-BY-SA**.
- **Trap 2 (`excludes_video_games`):** community programs often exclude games / reserve the setting. Example: **Pathfinder-ORC setting + WotC Fan-Content = ship-blocked**.
- **Already-clean today:** `sundered-reach` + `tidal-commonwealth` are **"Original WorldOS — free and open"** (`CC-BY`). `baldurs-gate` is `COMMUNITY-PROGRAM` + `share_alike` (ship-blocked).
- **Enforcement seam:** [`scripts/license_check.py`](../../scripts/license_check.py) (do not fork it — extend it).
