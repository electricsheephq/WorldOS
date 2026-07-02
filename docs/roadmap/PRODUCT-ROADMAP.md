# WorldOS Product Roadmap — the ladder, the sprints, the versions

> **The master navigation doc.** VISION.md says what the product IS and the bar it must clear;
> this doc says the ORDER we build it in — every sprint from here to GA and beyond, with a
> binding gate, an ordered issue list, and a version pin, so that ANY agent (Claude, Codex, GLM,
> human) can open this file, find the active sprint, and execute a lane without needing this
> repo's history in its head. Authored by the architect session 2026-07-03 (Fable), red-teamed
> (deep-reasoner adversarial pass), amendments incorporated. Owner reviews via the PR that
> introduced this file.

---

## 1. The product thesis

WorldOS is **"Baldur's Gate that writes itself, played through your own AI."** Three concentric
products, built in this order:

1. **The Game** — the BG-universe flagship: the deterministic SRD 5.2 engine + the OpenWorlds
   viewer + the native app + the PoE2 painterly combat demo. The 1.0 ladder ships this.
2. **The Platform** — the same game played through *any* agent (Claude Code today; Codex /
   OpenClaw / Hermes lanes in epic #911) and eventually hosted (milestone 25). The moat: the
   player brings their own agent + credentials, so there is **no central LLM cost** — the
   distribution economics are the platform bet.
3. **The Universe System** — universe creator + template packs (#644) + licensed universes
   (KOTOR #330, VtM #331, Shadowrun #332) + fan showcase (milestone 26). Post-GA revenue
   shape: original-world paid alpha (#712) with BG as the free flagship.

**The meta-product** (name it, because it is working): the measurement culture itself —
control-anchored art panels, story lenses, the behavioral gate, the FELT track. These
instruments are what let autonomous agents build a game to a bar. WorldOS is the game AND the
proof that the method ships games.

## 2. Version map (fixes the semver/rung muddle)

Engine releases stay **semver v1.0.x**; product rungs are **named releases pinned to an engine
version**. The old "v1.1 — The Table" milestone label was misleading (its content ships inside
v1.0.7–v1.0.8) and is retitled **"The Table (story/world systems)"**.

| Engine version | Sprint(s) | Product rung cut at this version |
|---|---|---|
| v1.0.5 | S1 | — (engine release: engagement + combat epic + art pipeline batch) |
| v1.0.6 | S2 + S3 | — (felt demo loop + combat readability) |
| v1.0.7 | S4 | — (The Table I) |
| v1.0.8 | S5 + S6 | — (The Table II + alive/latency) |
| v1.0.9 | S7 | **Player-Ready Beta** (2D-viewer scope — see §4/S7) |
| v1.0.10 | S8 (+S9 parallel) | **1.0 Playable Combat Demo** |
| v1.1.0 | S10 | **1.0 GA** |
| v2.x | post-GA phases | platform / universes / rendered-game (§6) |

## 3. Sprint execution protocol (how ANY agent runs a lane)

- **A sprint = a charter epic issue** (label `sprint-charter`) + its milestone. The charter
  carries: GOAL · BINDING GATE (as *runnable commands + evidence paths*, never vibes) ·
  ORDERED issues with lane labels · INVARIANT checklist · RISKS/owner-gates.
- **Lanes** (issue labels): `lane:engine` · `lane:story` · `lane:renderer` · `lane:app` ·
  `lane:qa` · `lane:art` · `lane:platform`.
- **An issue enters a charter only when dispatch-ready**: spec, acceptance criteria, evidence
  requirements, invariant notes. The charter author upgrades stale issues or they wait.
- **The loop** (unchanged, from VISION "How we build"): worktree off main → additive change →
  single-process pytest + `qa/fast_gate.sh` → PR → review-gated merge → prune. QA tiers per
  `docs/qa/FAST_GATE.md`; never report Tier-0/1 as a release verdict.
- **Anti-variance rule for release gates** (red-team amendment): a story gate is never a single
  duo — the deep 24-beat read must be corroborated by the sweep duo; if they straddle the bar,
  a third duo decides by median. Mech is always a combat-sprint median (n≥3). FELT gates are
  **control-anchored same-panel deltas** (beat the embedded disguised real-art control), never
  raw votes or absolute numbers.
- **Ledger discipline**: every scored run → `qa/scores_db.py`; every panel embeds a disguised
  real-art control; identity mappings live outside panel dirs.

## 4. The sprints

### S1 — Engagement Engined *(ACTIVE — closes the v1.0.5 campaign)*
- **Goal:** the engine's relationship/quest/camp machinery is *driven*, not narrated — the
  measured rri-a1-duo2 defect class (behavioral RED: 0 quest/relationship tool calls in 22
  beats) is closed by every-beat obligation cues.
- **Issues:** #1286 (✅ PR #1299) · #1288 (✅) · #1287 + #1285 (PR #1300) · #1160 WS3a rebase ·
  merge-queue closeout (#1282 #1283 #1293 #1294 #1295 #1297 #1298).
- **Gate (runnable):** re-freeze SHA → fresh 24-beat Opus duo: story ≥4.3 **and** behavioral
  GREEN (structural_completeness passes), corroborated per §3 anti-variance rule → 3× combat
  sprint median → VM 5-persona sweep → Mac native part-A → `qa/release_readiness.py` rollup →
  **cut v1.0.5** (CHANGELOG batch + tag + pre-release + evidence bundle).
- **Lane:** `lane:story` + `lane:qa`.

### S2 — The Felt Demo Loop *(graphics; milestone 31)*
- **ENTRY GATE (red-team amendment — do this FIRST):** the **render-delivery decision doc**
  (issue filed as the S2-entry blocker): how does the Unity render reach the player's screen —
  embedded Unity build in the app / render-service streaming frames into OpenWorlds / native
  Unity player? First-principles decision doc (options, data, diagram, debate, decision), NOT
  code. Today's path is one-way box→QA-frames; S2's packaging depends on the answer even
  though the Animator/VFX logic itself is delivery-agnostic.
- **Goal:** the demo's moment-to-moment feel — animation, VFX, death, motion — driven by real
  engine events.
- **Issues:** `/events` Action-Replay → Animator/VFX wiring (verb→clip map, `anim_hint`,
  DOTween `lastPath` glide, damage/VFX at engine cells) · death/defeat visual resolution
  (topple/fade) · #1296 edge-blending (Beautify composite grade / silhouette feather) ·
  #1284 prop-cell grounding · #1281 re-validation on a REAL church-located combat ·
  L7 motion-reel lens first run · ghoul combat-clip completion.
- **Gate:** combat-FUN checklist 6/6 with evidence frames (VISION §Graphics) + FELT re-panel
  where the hero frame **meets/beats the embedded real-art control same-panel** (votes are a
  secondary signal only).
- **Lane:** `lane:renderer` + `lane:art`.

### S3 — Combat Readability *(viewer; milestone 28 UI cluster; parallel-safe with S2)*
- **Goal:** the combat UI communicates state like a real CRPG on the 2D OpenWorlds surface —
  independent of the render-delivery answer.
- **Issues:** #595 condition badges + initiative row · #596 reaction pip / OA banner /
  concentration warning · #597 target-bind indicator · #599 dodge/dash/disengage/hide/shove
  verbs · #219 crit attribution + point-blank nits · #827 character-sheet button.
- **Gate (runnable, taxonomy fixed up front):** a blind AI playtester (`qa/ui_playtest.sh`,
  combat-seeking persona) completes a GUI combat with **zero bugs in the defined
  confusion-taxonomy**: dead-control · state-mismatch (UI disagrees with engine values) ·
  unexplained-wait >10s · mislabeled-affordance. Taxonomy classes tagged in `bugs.ndjson`.
- **Lane:** `lane:app` + `lane:engine`.

### S4 — The Table I: Rest & Memory *(milestone 29 retitled; sequel to S1)*
- **Goal:** camp/rest is a real subsystem and the world visibly remembers choices — the two
  cheapest-highest-leverage Table pillars, aimed at the engagement WARNs S1's measurement
  surfaced (camp never fired, world under-peopled).
- **Issues:** #611 camp_supplies + make_camp/forage · #609 honest camp clock · #612 + #613 +
  #614 relationship/reputation change-logs + panel · #616 ArcGate ladder · #775 retrieval
  surface revival (+#803 prerequisites) · **#753 latency BUDGET definition (pulled forward —
  red-team amendment: it is the documented #1 give-up driver; defining + instrumenting is
  cheap and it must be measurable before Beta gates on it).**
- **Gate:** a scored 24-beat duo where camp fires ≥1×, the change-logs populate, a companion
  arc-gate progresses, and the S1-era engagement WARNs (camp/world_peopled/faction) are clear —
  story ≥4.3 held per §3 rule.
- **Lane:** `lane:story` + `lane:engine`.

### S5 — The Table II: Agendas & Rhythm
- **Goal:** companions push back; sessions have rhythm; failure becomes story.
- **Issues:** #837 companion agendas (**includes the engine-side `proactive_beat` flag,
  write+read in the same change** — red-team amendment: "companion-initiated" must be an
  engine-mutated value, never fiction-read) · #838 session rhythm (recap/cliffhanger/epilogue) ·
  #839 fail-forward complications · #836 consequence surface · #141 parley relay · #608 parley
  echo.
- **Gate:** a scored duo with ≥1 engine-flagged proactive companion beat + recap/cliffhanger
  present + **tolkien `dramatic_momentum` dim ≥4.3** (an existing rubric dimension — red-team's
  "invented metric" objection was wrong on this one point; it printed 4.2 in tonight's
  scorecard).
- **Lane:** `lane:story`.

### S6 — Alive: Live Composition
- **Goal:** the screen always shows motion — #835 keystone (stream the DM's scene as written),
  wired against the #753 budget defined in S4; #561 effort tiers.
- **Gate:** measured perceived-latency inside the budget on the GUI loop (from
  `duration_api_ms` + streaming-visible evidence); no dead wait beyond budget anywhere in a
  full playtest.
- **Lane:** `lane:engine` + `lane:app`.

### S7 — Player-Ready Beta *(cuts the Beta rung, on ~v1.0.9)*
- **SCOPE (red-team amendment, explicit):** Beta's "no broken moment" bar applies to the
  **shipped 2D OpenWorlds surface in the built .app**. The Unity render is NOT in scope for
  Beta — render-in-app is a Demo-1.0 (S8) gate. This resolves the ladder contradiction; VISION
  is patched to say so.
- **Issues:** #640 multi-campaign freeze (P0) · #307/#308/#310 playtest fixes · #288/#284
  responsive · #279/#174 icon registry · #503/#502 chronicle/narration polish · onboarding
  self-teach pass · "how a player plays" run-flow doc (per surface: app / CLI / bring-your-own-
  agent) · **latency budget met** (from S4/S6).
- **Gate:** VISION's Beta rung verbatim on the scoped surface: built .app, a no-prior-knowledge
  dogfood arc with no broken moment, honest felt session; 5-persona sweep green at the beta bar.
- **Lane:** `lane:app`.

### S8 — Demo Assembly *(cuts 1.0 Playable Combat Demo, on ~v1.0.10)*
- **Goal:** assemble the demo per the render-delivery decision (made at S2 entry): a modular
  multi-room dungeon, playable in-app, on placeholders, backdrop scorecard passing per room.
- **Issues:** render-in-app integration (per the decision) · demo dungeon authoring (4–6
  linked room-units with door-cell graph: crypt/church/tavern/bosshall + 2 new) · monster wave
  2 (cast to ~10, registry-complete) · day/night in-app · #441 blind traversal + combat build
  gate · backdrop scorecard run per demo room.
- **Gate:** VISION's 1.0 rung verbatim: playable modular combat scene in-app on the PoE2 stack,
  placeholders OK, backdrop scorecard PASS per room, combat-FUN 6/6 **in-app**, FELT
  control-anchored parity on the hero frame.
- **Lane:** `lane:renderer` + `lane:app` + `lane:art`.

### S9 — Atelier: Craft Above the Controls *(milestone 33; parallel with S7/S8)*
- **Issues:** #1241 Atelier kit (3D modular base + albedo LoRA paint-over) · #1243
  tile-ControlNet finisher · #1217 multi-room composition craft · #1218 stray-item control.
- **Gate:** adopted plates beat their real-art controls by ≥+1 median on ≥2 consecutive panels;
  FELT control-parity on hero frames.
- **Provider-fallback note (red-team amendment):** Scenario + Gemini are single-vendor
  dependencies for the plate pipeline; the registry-by-slot invariant makes output swaps cheap,
  but the *generation* lane needs a named fallback (ComfyUI/SDXL on GEX44 — already
  provisioned for #1243) documented in the graphics roadmap.
- **Lane:** `lane:art`.

### S10 — GA Assembly *(cuts 1.0 GA = v1.1.0)*
- **Issues:** placeholder→real art registry swap (through the S2/S9-proven pipelines) · #151
  notarized distribution · #134 Sparkle updates · #591 parity meta-check · **#643 Scoring v2
  as the GA-grade instrument** (RRI saturation + satisfaction provenance fixed BEFORE the GA
  verdict is measured on it) · #1137 save-compat CI guard · #1122 security triage (also gates
  the hosted-runtime track) · multi-agent GA slice (see platform track).
- **Gate:** VISION's GA rung: Demo workflow on real art + Beta story/world bar + notarized +
  parity + **the bring-your-own-agent surface documented and the Claude Code lane verified
  end-to-end** (red-team amendment: GA-block ONE proven provider lane, not the 5-lane epic).
- **Lane:** all.

## 5. The platform track (parallel, after S7)
1. **#911 multi-agent plugin** — GA slice = docs + Claude lane verified (S10); Codex CLI lane
   next (the cheapest second runtime), then OpenClaw/Hermes post-GA.
2. **Milestone 25 hosted runtime** — strictly behind #1122 security triage; the
   runtime-host decision (#706) is a first-principles doc. Economics note: hosted sessions pay
   the DM cost-per-beat centrally — the bring-your-own-agent default (#911) is the hedge; a
   hosted tier prices against measured $/beat from the ledger.
3. **Milestone 26 creator + showcase** — post-GA; #711 licensing policy is an owner/legal gate.
4. **#32 Discord / #31 multiplayer** — vNext after hosted runtime proves session economics.

## 6. Beyond GA (the future, so the trajectory is legible)
- **Phase A — "Any Agent, Anywhere":** all #911 lanes + hosted runtime + Discord distribution.
- **Phase B — "The Universe System":** creator (#713), template packs (#644), licensed
  universes v2.1–v2.4 (#330–#332), fan showcase policy (#711), paid-alpha original world (#712).
- **Phase C — "The Rendered Game":** #645 north-star render pipeline (world → playable rendered
  turn-based video game); #1045 Unreal fidelity tier stays deferred until the Unity path caps.
- The pixel-art tier (#1145) remains a filed option, not a lane.

## 7. Owner Gate Register (human-gated items — clear these AHEAD of need)
| Item | Needed by | What the owner must do |
|---|---|---|
| Apple Developer cert + notarization creds | S10 start | provision cert into Keychain (#151) |
| Sparkle signing key | S10 start | generate/store key (#134) |
| Licensing: Owlcat/OpenWorlds reference assets | before S8 art ships | #122 audit sign-off |
| Fan-showcase / user-content policy | milestone 26 | #711 policy call |
| Asset-Store purchases (if S2/S9 need packs) | as flagged | purchase + import to box project |
| GPU-box capacity/renewal | continuous | GEX44 lease + Unity seat |
| Paid-alpha pricing | Phase B | business call (#712) |

## 8. Standing gaps register (filed as issues alongside this doc)
Render-delivery decision (S2 entry) · Action-Replay/Animator wiring · death-resolution visual ·
L7 motion-reel first run · monster wave 2 + ghoul clips · demo dungeon authoring ·
`time_of_day` on `/combat-surface` · run-level duo resume (constructive half of #1285) ·
S3 confusion-bug taxonomy · sprint-charter convention (this doc + labels).
