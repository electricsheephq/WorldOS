# Act III — The Universe Platform (design, architect 2026-07-08)

> **Status:** design (not yet scheduled — Acts I/II precede it). This is the "changes how people make games"
> thesis made concrete. Sequencing source of truth stays `PRODUCT-ROADMAP.md`; this doc is the *architecture*
> of the platform Act III builds, so any agent picking up Act III starts from a real design, not a stub.
> Supersedes the "re-anchor existing epics" placeholder. Anchors: #644 (packs), #645 (render pipeline), #330-332
> (KOTOR-class universes), #1145 (2D tier), milestone 25/26 (hosted + creator).

## 1. The thesis (why this is a platform, not just a game)
BG-caliber D&D is the *first universe*, not the product. The product is the **engine that generates and runs
universes** — deterministic SRD-5.2 rules today, but the rules layer is one pluggable module. WorldOS becomes
a platform when a third party can, without touching engine code: (a) **author** a universe (world + rules
skin + content packs), (b) **play** it across the T0-T3 tiers, and (c) **distribute** it so others play and
extend it. The two Act-II systems already ARE the platform substrate — they were built as game features but
their shape is platform-shaped:
- **The Template Library = the pack content model.** `library/` is already pack-shaped (pack.json
  {name,version,license,provenance} + typed entries + eval scores). A "pack" a creator ships and a "library"
  the engine assembles from are the SAME artifact. #644 is not new architecture — it is `library/` with a
  distribution endpoint.
- **The Harvest Loop = the community quality bar.** The eval-gated promotion pipeline (HV1-HV5) is exactly the
  admission control a marketplace needs: community content enters the same disguised-canon-anchored panels,
  and only eval-passing content earns a tier. "Scored-then-promoted" is how you keep a creator ecosystem from
  drowning in slop — the hardest unsolved problem in UGC platforms, and we built its instrument first.

## 2. What is ALREADY true that makes this near (the load-bearing invariants)
The platform is reachable BECAUSE of decisions already shipped — not despite them:
- **Additive-by-default + `_StrictModel` + tolerant load.** Every feature defaults to today's behavior; old
  snapshots round-trip. This is the *versioning contract a platform requires* — a v1 world plays on a v2
  engine. Already the law, already tested.
- **Engine = SOLE WRITER via atomic snapshot.** State is one serializable artifact under a campaign lock.
  A universe's entire live state is portable, forkable, hostable — the precondition for hosted play (m25/26).
- **Gates read only engine-mutated gauges, never fiction.** The rules layer can be RE-SKINNED (a different
  d20 variant, a PbtA-style move engine, a 2D-JRPG stat block) without the trust boundary moving — gauges stay
  the contract, prose stays advisory. This is what makes "one engine, many universes" safe.
- **Tiered presentation (T0 text → T3 rendered) over ONE engine + ONE DM.** The DM runs everything in text;
  graphics are a presentation upgrade. So a creator's universe plays at whatever tier their content supports,
  and the text tier is the universal floor — the platform never has an unplayable world.
- **`room_recipes` + registry + the asset-gen pipeline** already turn a prompt into a rendered environment;
  the backdrop cadence is a content-production line, not R&D.

## 3. The three platform surfaces (the actual Act III build)
### S-P1. Engine-as-package (the SDK)
WorldOS ships as an installable engine + plugin (it already runs via `claude --plugin-dir`; the step is a
versioned, documented, semver-stable **public tool/schema contract** — the MCP surface becomes the SDK).
Deliverable: a pinned public API (the player-facade tools + the DM tool surface) with a compatibility promise,
a `worldos init <universe>` scaffold, and the SRD rules module cleanly separable so a creator can swap/extend
it. De-risk: the tool schema is already budget-ratcheted (SYN-02) — freezing it is a decision, not a rewrite.

### S-P2. The Creator surface (author without code)
A world/pack authoring surface on top of the existing seed/questgen/library primitives: define a world
(canon, factions, NPCs, quest variants), generate-or-import content, run it through the SAME harvest panels
to see its eval scores BEFORE publishing, and export a pack. This is the OpenWorlds viewer's meta-UI extended
from "play" to "author" — reusing the surface that already exists. The creator sees the exact quality bar the
platform enforces, which turns eval-gating from gatekeeping into a *tool* ("here's why your villain scored 2.9
— thin dialogue; here's the rubric").

### S-P3. Distribution (packs + hosted play)
#644 packs get a registry/marketplace shape: install a pack → its `library/` entries become questgen/lookup
sources (HV4 already wired the consumption side, default-off, tier-weighted). Hosted play (m25/26) uses the
portable snapshot to run a universe server-side. Licensing/provenance already ride in every entry — the
marketplace's legal + attribution layer is already in the data model.

## 4. Proof-of-universality: KOTOR-class (#330-332) + the 2D tier (#1145)
The test that WorldOS is a *platform* and not a D&D engine with hardcoded assumptions: build a SECOND universe
with a different rules skin (Star Wars d20 / a KOTOR-style system) and a different presentation tier (or a 2D
JRPG tier, #1145) reusing the SAME engine, harvest loop, and creator surface. If a KOTOR universe needs engine
forks, the platform claim is false and we find out cheaply. This is the acceptance test for Act III, exactly
as the T3 blind-playtest is the acceptance test for Act II.

## 5. Sequencing + the MVP cut
**Platform-MVP (the minimum that proves the thesis):** S-P1's frozen public contract + S-P3's pack
install/consume (both largely exist) + ONE externally-authored micro-pack that installs and plays. That is a
*days* deliverable once Act II's flywheel is PROVEN, because the substrate is built.
**Full Act III:** the creator surface (S-P2), hosted play (m25/26), the second universe (#330), the 2D tier
(#1145), the 100-environment library at scale.
**Ordering law:** Act III does not start until (a) the flywheel is PROVEN (HV4 library-vs-gen A/B shows
library content scores at-least-parity — if it scores worse, the pack model is broken and Act III's premise
fails; this A/B is the single most important gate on Act III), and (b) the render tier has a box-independent
validation path (else T3 packs can't be QA'd without the one GPU box).

## 6. The two de-risks Act III inherits from Act II (must clear FIRST)
1. **Flywheel validity (blocks the whole pack model):** run the HV4 A/B. Library-assembled game must score
   ≥ pure-gen on the lenses AND show measured lower AI-dependence, or the "content compounds" thesis — and
   therefore the marketplace value prop — is unproven. Highest-priority unrun eval in the project.
2. **Render-tier box-independence (blocks T3 packs at scale):** today the entire rendered tier hostages on one
   GPU box + a human to power it (bus-factor-1, currently down). Before creators can ship rendered packs, the
   render+validate path must run on any GPU (headless URP render-to-PNG / cloud build) so pack QA isn't
   single-machine-gated.

## 7. What this doc changes right now
Nothing ships from Act III yet (Acts I/II precede it). What changes: the Act III epics (#644/#645/#330) now
point here for architecture, the "re-anchor existing epics" placeholder in PRODUCT-ROADMAP §6 is replaced by
this design, and the two Act-II de-risks (§6) get promoted from "nice-to-have evals" to **hard preconditions
on Act III** — which reprioritizes the HV4 A/B and the box-independence work as the bridge from Act II to the
platform.
