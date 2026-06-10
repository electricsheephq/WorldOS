# WorldOS FULL-ENGINE ADVERSARIAL AUDIT — MASTER REPORT (SYNTHESIS)

- Date: 2026-06-11. Repo: /Users/lume/ClawDnD-val. Tasked SHA f24a102; **all verification ran against actual main HEAD a245a2c** (3 commits ahead; delta = #760-#764 incl. the #749/#763 heartbeat-decontamination fix — accounted for per-finding; F12-14 and F11-1 were explicitly re-scoped onto it).
- Inputs: 14 unit audits, each independently skeptic-verified (/tmp/engine-audit/unit-NN-verified.md). Dedup base: /tmp/engine-audit/open-issues.txt (146 open issues incl. in-flight #748-#758). Persona evidence: rc1 worldos-qa-results-fa97b34 bugs.ndjson + rc2 worldos-qa-results-033e4ba score-*.json.
- Totals: **188 findings produced -> 141 confirmed, 46 corrected, 1 refuted** (F4-15 thread-id collision). After cross-component dedupe: **169 distinct backlog items** (11 cross-unit merges below + 2 in-unit folds F14-20->F14-8, F06-11->F06-10).

---

## 1. EXECUTIVE VERDICT

The engine core is genuinely healthy where it counts: atomic persistence (temp+os.replace under campaign_lock, ~1 ms saves), per-campaign locking, dice/crit math, the damage pipeline, copper-exact currency math, and the engine hot path (~1-4% of a beat, re-benched) all audited clean, and the sole-writer/additive invariants held at every site checked. The defect mass concentrates in four cross-cutting themes:

1. **Written-but-never-read state.** The engine faithfully tracks things that nothing consumes: Bless/Shield-of-Faith effects no resolver applies (SYN-06), sneak-attack dice no combat surface reads (F01-5), approval gauges with no organic mutation path (F06-2), scheduled consequences that structurally never fire (F14-4), quest evolution no tool can reach (F05-1), a retrieval surface with zero organic calls (F07-7), and an economy toolchain entirely dark in real play (F09-4). This is the dominant story/mech-score theme: the machinery for 4.3/4.5 exists and is starved at the read or adoption seam.
2. **Path divergence.** Five character-seat paths and two monster-spawn paths each miss a different subset of required steps (abilities, gear, saves, PB, arcs, dossiers — F02-1/2/4, F01-2/11, F06-1); three beat-loop wrappers each miss a different subset of hardening (heartbeat, soft-tick, advisories, timeouts, aborts — F12-3/4/5/6, F12-1/8). The fix shape is the same everywhere: extract the shared helper, stop patching one path.
3. **Beat reliability.** ~10.5% of DM invocations produce no usable beat, 401 error text flows to chat AS DM prose, and the wrapper's flat 200s timeout kills ~18% of healthy routine beats (SYN-01, F12-1). This cluster — SYN-01 + F12-1 + F12-3/4/5/8 — is the single highest-leverage block in the backlog: it maps directly onto the rc1/rc2 persona complaints (dropped actions, blank spinners, dead sessions) and is almost all S-effort wrapper work.
4. **Unbudgeted token mass on a generation-bound path.** A fixed ~40K-token tool-schema slab (54% of the 73.7K lean floor, SYN-02), 45K-token roster dumps and roster-as-error payloads (SYN-03), full event prose re-sent every beat (SYN-04), and uncapped every-beat history surfaces (SYN-08, SYN-09) — with no latency ledger to enforce #753 against (F13-4).

Exactly **one P0**: the image_render gate is structurally un-passable on the VM release lane (F11-1) — a release-blocker today regardless of engine quality. What is NOT broken: store/persistence core, dice and crit math, initiative (#733), encounter/XP math, lock discipline, wire contracts, and engine execution speed — the latency problem is input mass and round-trips, not engine time (re-confirmed; the refuted-ideas list stands: no prefetch helper, no --fast flag, no mid-campaign model switch).

---

## 2. CROSS-COMPONENT MERGES (dedupe verdicts)

| Merged ID | Absorbs | Kept root cause | dup_status |
|---|---|---|---|
| SYN-01 Dead-beat masking & failure classification | F12-7 (P1) + F12-14 (P2) + F13-5 (P2) | F13-5's sharpened mechanism (401 result text is NON-empty -> bypasses empty-only retry AND fallback -> chatlogged as DM prose) + F12-14's post-#763 per-lane modes + F12-7's dead stamp | **enriches #757 + #745** |
| SYN-02 alwaysLoad schema mass | F13-1 + F14-6 | F14-6's corrected math (docstring diet alone -> only ~145KB; deferring the dead tail is load-bearing) + F13-1's cold-open-pinning constraint | enriches #753 |
| SYN-03 Canon-roster surfaces unbounded | F10-3 + F13-2 + F13-3 + F14-1 | F14-1 (both list + miss paths, soft-error invisibility) + F13-3's corrected 2-site scope | new (#316 = viewer, cross-link) |
| SYN-04 Manual events ride every beat | F05-3 + F07-3 | F05-3's trigger/content root (events.py:49-50 manual => always True) + F07-3's first_presented_day stamping spec | new |
| SYN-05 Action economy per-tool not per-turn | F01-3 + F03-9 | F01-3's Combat.action_purpose design + F03-9's casting_time resolution & use_action interplay (live in BOTH directions) | new |
| SYN-06 Engine-tracked buffs inert | F01-6 + F03-1 | F03-1's corrected spec (linked_to_concentration flag + BOTH sweep-loop extensions; the naive expiry never fires) + F01-6's curated rider registry | new |
| SYN-07 Parley/actor binding instability | F10-2 + F12-15 | Two coupled mechanisms behind the mid-interaction switches: engine parley has no npc_id/attitude input; facade re-resolves max(updated_at) per call | **enriches #751 + #640 + #319** |
| SYN-08 Unbounded campaign-history surfaces | F07-5 + F07-11 + F13-6 + F14-16 + F14-17 | read_log_all full re-parse (no tail short-circuit) + count-not-byte recap + uncapped durable/tail blocks; logs append-only, never rotated | new (relates #17) |
| SYN-09 Echo-back return mass | F07-12 + F13-7 + F14-14 | F14-14's full inventory + F13-7's persist_beat test-assertion caveat + F7-12's bare-KeyError leg | new |
| SYN-10 recall_npc split-brain | F07-2 + F10-5 | identical root found twice (ledger who=NAME for dialogue, ID for facts); F10-5's ref=<id> belt added | new; hard prereq of F07-7 |
| SYN-11 Bloomridge dangling connection + silent ingest skip | F04-14 + F10-10 | identical content defect found twice; F10-10 adds the content.py:684-688 silent-skip diagnostic + CI walker | new |

Fallback-masking reconciliation vs #757 (explicit task): nothing re-files the masking itself — SYN-01 carries only the three unowned legs (classification, blank/hidden-row guard, dead stamp) as an **enrichment** of #757's planned fix, which is unimplementable until the three QA-runner chatlog overrides are deleted.

### Merged-finding deep specs

#### SYN-01 [P1|high|M] Dead-beat masking & failure classification (F12-7 + F12-14 + F13-5)
- gates: no_give_up + cross_persona_sat + behavioral-gate honesty. ~10.5% of DM invocations (28 no-result + 3x401 of 294 Mac files; VM 18+5) produce no usable beat; each is a 100-300s player-visible wait for nothing or for an auth error rendered as narration.
- what_is_broken: (a) a 401-class failure's error text appears in chat AS DM PROSE; (b) on play.sh a dead beat now (post-#763) yields an unflagged EMPTY dm row; on play_party (no heartbeat — F12-4) the fallback still recycles the PREVIOUS beat's prose into a row the client hides (engine_logged dedup -> app.jsx drops it); (c) the fallback_recovered honesty stamp never lands in any QA runner, so #757's planned gate discount is unimplementable.
- why_broken: 401 results carry NON-empty result text (subtype:"success", is_error:true, api_error_status:401 — verified verbatim) so they bypass BOTH the empty-only retry (qa/run_duo.sh:176-190) and the empty-only fallback (qa/lib_beat_driver.sh:138); record_dm_reply accepts blank text (scripts/play.sh:437/:501); run_duo.sh:135, qa/ui_playtest.sh:138, qa/run_party.sh:169 redefine a 3-arg chatlog AFTER sourcing the lib, discarding clawdnd_chatlog_dm's extra_json (lib:295); zero consumers of the flag in qa/assert_behavioral.py.
- how_to_fix: (1) in the shared resolve path, parse the final result event FIRST — is_error/api_error_status => beat FAILED: never chat the error text, never fallback-recycle, surface "DM needs re-auth", count into F13-4 beats_failed; budget pre-check before launch. (2) guard record_dm_reply against blank text; when FALLBACK_RECOVERED=1 AND prose dedups as already-logged, emit a wrapper-authored VISIBLE failure beat + {"beat_failed":true} (UX with #757/#745); preserve the genuine #357 win via pre-beat log-tail snapshot. (3) delete the 3 chatlog overrides (lib chatlog verified drop-in superset) + add an assert_behavioral counter/report (gate policy stays #757's).
- test_strategy: 401 fixture -> beat recorded failed, chat row contains neither the error string nor recycled prose; pre-beat log has P1 + both attempts die -> visible failure row (not P1, not empty, not hidden); CLAWDND_FALLBACK_RECOVERED=1 + clawdnd_chatlog_dm in each runner's function set -> row carries the flag (all 3 FAIL today).
- effort: M (three S legs) ; confidence: high ; depends_on: F12-4 (party lane converges to empty-row mode after it) ; dup: enriches #757 + #745.

#### SYN-02 [P1|high(mass)/med(seconds)|M] alwaysLoad pins ~40-44K tokens of tool schemas into every DM request (F13-1 + F14-6)
- gates: latency budget (#753 line-item #1) — ~54% of the measured 73.7K-token lean first-request floor; 37% of the 200K window gone before beat 1.
- why_broken: 141 @mcp.tool in servers/engine/server.py -> list_tools JSON 160-175KB (175,202B exact in plain serialization; docstrings alone 89,713B exact); per-SERVER "alwaysLoad": True at scripts/play.sh:115, scripts/play_party.sh:161, qa/run_duo.sh:109; no tool-level granularity exists.
- how_to_fix: docstring diet (~400 chars/tool) is necessary but NOT sufficient (schema overhead dominates: ~145KB after diet); the load-bearing half is deferring/unpinning the dead tail to a NEW additive deferred server id (same process/store, campaign_lock, frozen clawdnd-engine id untouched). Usage-rank the split WITH THE COLD-OPEN LOOP IN MIND: start_world/get_prelude/get_quest_hooks/spawn_monster/generate_image are cold-open-path — keep pinned or explicitly measure the re-introduced ToolSearch turns inside the 22-turn cold-open (the #745/#748 give-up band). Gate on a same-SHA/seed duo A/B via F13-4 columns. CI byte-budget test on list_tools.
- test_strategy: pytest list_tools JSON budget (fails today); duo A/B with ToolSearch-count + api_ms columns.
- effort: M ; confidence: high(mass)/med(wall-clock delta) ; depends_on: F13-4 ; dup: enriches #753.

#### SYN-03 [P1|high|S] Canon-roster surfaces unbounded — 180KB lists, roster-as-error, MCP-cap errors (F10-3 + F13-2 + F13-3 + F14-1)
- gates: latency (cold-open band; ~45K tok + ~5 extra Read-paging turns) + no_give_up (8 MCP-cap error rows across 249 calling transcripts; each costs a beat).
- why_broken: list_canon_characters returns ALL records verbatim — content.py:155-197 uncapped walk, server.py:2275-2289 no limit/q (180,630B / 2,076 records re-measured exact; playable_only still 173KB); misses dump the roster as the ERROR payload: load_canon_character miss server.py:2360 (28,187B vs HIT 271B) + start_character pickup miss :1550; the harness offloads oversize results to a tool-results file and the DM Read-pages it back at cold-open. Soft-error dicts are is_error=false -> invisible to the behavioral gate (F14-13).
- how_to_fix: additive limit=100/50 + q/name_contains + {total, returned} (find_npcs at server.py:2291+ already ships limit:int=50 — in-file precedent); resolve-then-suggest on every miss (exact-ci -> success; unique substring -> resolved_from; else difflib top-5 did_you_mean + available_count, KEEP the error key — play.sh reads it); same bound on start_character miss sites (1526/1549); flip consumers via SKILL.md seat step + QA prompts (engine default unchanged = today's dump).
- test_strategy: 300-record synthetic world: miss <2KB with did_you_mean<=5; limit=100 -> returned==100, total==300, <64KB; small-world byte-identical; cold-open smoke asserts zero offloaded tool-results files.
- effort: S ; confidence: high ; dup: new (#316 = viewer roster UI, cross-link only).

#### SYN-04 [P1|high|M] All authored events are trigger-"manual" and ride every beat as full prose (F05-3 + F07-3)
- gates: latency (~6.5KB ≈ 1.6K tok/beat — 78% of the base scene_context bundle, byte-reproduced twice) + story pacing (5 Kingmaker-grade decisionals incl. Raphael's bargain surfaced at minute one; contradicts living-arcs.md:41-43).
- why_broken: events.py:49-50 trigger_holds("manual") unconditionally True; present() uncapped (events.py:66-77); scene_context embeds full projections every beat (server.py:9127-9128, lean default makes scene_context the mandatory first action — lib_beat_driver.sh:421); BG ships 5/5 manual + tidal 2/2; no presented-before memory exists.
- how_to_fix: three legs. (a) content: real triggers (day_reached/reputation_at/flag_set) on BG/tidal events + content lint (non-manual or <=1 manual per world). (b) read valve: surface <=1 manual event per call + manual_queued count with rotation (resolve_event resolves by id from c.events -> queued events stay resolvable). (c) memory: additive Event.first_presented_day: Optional[int]=None stamped under campaign_lock (check_companion_arc precedent); already-presented events return a stub {id, prompt_head, option labels, note}; standalone present_events keeps the full payload; update scene_context's "NEVER writes" docstring in the same PR. No GUI consumer of present_events found — not a frozen-wire break.
- test_strategy: 2 manual events -> second scene_context shrinks events >70%; 3-manual -> 1 + manual_queued:2 rotating; flag_set presents full exactly once post-arm; absent-field snapshot round-trip; seeded BG day-1 events section <=~1.5KB.
- effort: M ; confidence: high ; dup: new (#753 = budget definition only).

#### SYN-05 [P1|high|M] Action economy is per-tool, not per-turn (F01-3 + F03-9)
- gates: mechanical>=4.5 (explicit rubric item). Every caster-martial turn can double-act: cast+attack, attack+cast, double-cast, skip->attack all legal; bonus-action spells (Healing Word) wrongly consume the action AND use_action then wrongly REFUSES a legitimate follow-up (server.py:3723-3726) — live in both directions.
- why_broken: attack() gates only on action_attacks_made (server.py:3910-3923); cast_spell never READS action_used and sets it unconditionally with no casting_time branch (5305-5337, 5418-5419); Combat records THAT an action happened, not WHAT (models.py:1046-1060); casting_time data exists (257 action / 23 bonus / 40 min / 15 hr) and is never read for economy; test_action_economy.py has 4 tests, 0 cast_spell.
- how_to_fix: additive Combat.action_purpose: Literal=""; in the combat-active branch resolve casting_time: bonus->bonus_action_used; action->reject if action_used or purpose in {cast,skip} or attacks_made>0 (honor surge_actions as the escape); minute/hour->refuse in combat; attack() symmetrically rejects on purpose cast/skip; next_turn resets. Out-of-combat byte-identical; old snapshots round-trip.
- test_strategy: red-first trio (cast->cast, cast->attack, attack->cast rejected) + HW->Fireball allowed + HW->use_action(action) allowed (currently refused) + surge enables 2nd cast + declared-action->Extra-Attack still green + snapshot round-trip.
- effort: M ; confidence: high ; dup: new.

#### SYN-06 [P1|high|M-L] Engine-tracked buffs are mechanically inert (F01-6 + F03-1)
- gates: mechanical>=4.5 — the engine advertises Shield of Faith/Bless/Shield in active_effects, then authoritatively rolls without them; top concentration spells are duration-tracked theater.
- why_broken: ActiveEffect (models.py:472-538) has NO generic modifier fields — buffs are unrepresentable, not merely unautomated; the only special cases are "mage armor" name-match in _effective_armor_class (server.py:3773-3804) and Guiding-Bolt advantage (combat.py:47); saving_throw (5523-5544) and concentration_save (4372-4393) consult no effects; multi-target children (Bless on 3 allies) cannot expire with the caster's concentration — both the inverse sweep (3620-3621) and drop_concentration's loop (4427) match ONLY repeat-save markers (the naive expiry mechanism provably never fires).
- how_to_fix: additive ActiveEffect fields ac_bonus:int=0, roll_bonus_dice:str="" (+attack/save twins per F01-6's split) copied at cast (~5393) from a curated <=4-spell rider registry mirroring _ADVANTAGE_GRANTING_SPELLS (Bless, Bane, Shield of Faith, Shield); AC sums ac_bonus; attack/saves fold roll_bonus_dice — engine rolls and surfaces the d4 component. NEW additive linked_to_concentration flag on child effects + extend BOTH sweep loops to expire flagged children. drop_concentration sweeps; old snapshots round-trip.
- test_strategy: SoF target AC==base+2; blessed save/attack detail shows the d4; conc break expires children via both paths; round-trip.
- effort: M-L ; confidence: high ; depends_on: none (F03-13 Shield-reaction depends on THIS) ; dup: new.

#### SYN-07 [P2|high|M] Parley/actor binding instability (F10-2 + F12-15)
- gates: story_craft + cross_persona_sat; the engine-side anchor for #751's "Parley NPC can switch mid-interaction" and the facade-side mechanism behind the 6 observed silent character switches (#640 class).
- why_broken: (a) engine: generate_parley_options (server.py:6636-6731) accepts no npc_id, ignores attitude for DC (hostile -80 and helpful +80 yield the same menu), and `situation` (:6639) has zero body references while every real call ships 40-100 words into it (48 transcript calls). Docstring documents DM-supplied difficulty — design gap + dead param, not a broken contract (hence P2). (b) facade: player_server.py:53-62 _campaign() = max(updated_at) on EVERY tool call; with ACTOR_ID set, _pc() returns None when a parallel campaign takes the lead -> companion silenced / actor rebinds mid-session. The wrappers fixed this selector class via store.active_campaign_id (#640); the facade kept the heuristic.
- how_to_fix: engine: additive npc_id="" + payload npc:{id,name,attitude band,attitude_value,met}; attitude-derived default difficulty as a band shift (explicit difficulty wins); unknown id degrades; echo situation; npc_id absent -> byte-identical. Facade: additive CLAWDND_CAMPAIGN_ID env pin (unset -> today's heuristic); wire first in play_party companion cfgs (:262-269) where the id is known at config-write time.
- test_strategy: hostile/-60 default -> hard band; explicit overrides; absent -> today's payload; red-first two-campaign fixture: ACTOR_ID=X + CAMPAIGN_ID=A -> my_sheet resolves X while B is fresher.
- effort: M ; confidence: high ; dup: enriches #751 + #640 + #319.

#### SYN-08 [P2|high|M] Unbounded campaign-history surfaces on the every-beat path (F07-5 + F07-11 + F13-6 + F14-16 + F14-17)
- gates: latency — the lean spine creeps invisibly as campaigns age (30+ sessions -> 30-60KB/beat projection) and the read side is strictly linear in campaign size on the every-beat path.
- why_broken: read_log_all opens + pydantic-validates EVERY line of EVERY session file to return the last 8, 2-4x/beat, no tail short-circuit (store.py:347-393; server.py:9025-9056; 25ms @ 2,518 rows today, linear); session_recap bounds COUNT (12) not SIZE — 12x4KB entries -> 48,631B reproduced live (recap.py:44-70); scene_context's durable block has no cap on npc_relationships (every met NPC forever) or open-quest objectives (server.py:8906-9020, #763 added no caps) and the recent_narration tail returns {"text": e.text} verbatim, count-bounded only; session logs are append-only, never rotated.
- how_to_fix: (a) read_log_all(tail=N) — newest-first file walk, stop at tail x slack, return chronological; full walk stays default; equivalence-tested. (b) recap: per-entry ~400-char sentence-boundary cap + ~6KB total budget (defaulted params) + one deterministic engine-state line (day/time/open quests/last decision — engine-mutated values only, NO LLM summarization). (c) durable: cap npc_relationships top-24 by (|attitude_value|, recency) + npc_relationships_omitted; open_quests stay complete but <=3 open objectives + objectives_omitted; pure derivation. (d) tail: per-entry soft cap + total budget DEFAULT-OFF (lean DM's story memory — ride a long-campaign duo A/B before any default change). Rotation/archival deferred.
- test_strategy: tail-read == full-walk[-N:] on multi-file fixtures + row-parse counter; 12x4KB recap <= budget with newest intact; 60-met-NPC fixture -> len<=24 + omitted==36; default-OFF tail byte-identical.
- effort: M ; confidence: high ; dup: new (relates #17 — different layer; distinct from #749/#763 which govern what ENTERS the log).

#### SYN-09 [P2|high|S] Echo-back return mass (F07-12 + F13-7 + F14-14)
- gates: latency — ~110K tok of corpus echo; log_event is the MOST-CALLED tool (195x) and echoes the DM's own prose back; remember/forget return the entire memory list (~6KB at 60 facts); persist_beat echoes O(items x whole-memory) and bare-KeyErrors on a malformed item (aborting batched writes); update_character returns the full ~4KB sheet.
- why_broken: server.py:7212-7214 {"logged": entry.model_dump()}; remember/forget return full ch.memory (:6372); persist_beat remembered.append(full list) per item + mem["character_id"] bare KeyError where siblings raise ValueError; update_character:2653. Consumer grep: NO non-test consumer reads any echo (gate parses transcripts; viewer tails session files).
- how_to_fix: slim returns — log_event {session_id, event_id, kind, ok}; remember/forget {id, name, added/removed, memory_count, memory_tail(<=5)}; mem.get -> ValueError; update_character {id, applied, recomputed} + vitals; soft cap-note at 50 facts suggesting forget (engine never deletes). SCOPE: persist_beat's echo is test-asserted (test_beat_roundtrip.py:307/329/346) — trim it in a separate, explicitly-scoped commit or leave; grep qa asserts for the LLM-facing shape before landing.
- test_strategy: shape contracts <300B; {"fact":"x"} item -> ValueError; on-disk entries unchanged via existing round-trips; one gate-duo green pre-merge.
- effort: S ; confidence: high ; dup: new.

#### SYN-10 [P2|high|S-M] recall_npc split-brain — dialogue keyed by NAME, facts by ID (F07-2 + F10-5)
- gates: story_craft at depth (returning-NPC continuity — the tool's stated purpose); LATENT today (0 organic recall_npc calls — F07-7) but a hard prerequisite for the retrieval-adoption push.
- why_broken: ledger.py backfill writes who=e.speaker for dialogue (engine sites pass .name — server.py:3293/4277/4321/4349; models.py:1077 "id or name") vs who=ch.id for npc_facts (ledger.py:190-200); recall_npc WHERE who=? exact (130-143). Reproduced: "Withers" -> dialogue only; "npc-withers" -> facts only. Live ledger scan: 71 speaker rows -> 1 id / 40 name / 30 free-text.
- how_to_fix: query-time resolution in ledger.recall_npc — read-only load_campaign, match id OR casefolded name, WHERE who IN (id, name) COLLATE NOCASE, fall back to single key; belt: backfill stamps ref=<id> when the speaker resolves to a roster name, match ref too. Derived-index only; sole-writer untouched; "Guard 2" never cross-matches.
- test_strategy: char(id=npc-x, name=Xara) + fact + dialogue -> BOTH query keys return BOTH rows; free-text speakers unaffected.
- effort: S-M ; confidence: high ; depends_on: land with/before F07-7 adoption ; dup: new.

#### SYN-11 [P3|high|S] Bloomridge dangling connection + silent area-ingest skip (F04-14 + F10-10)
- gates: content integrity; minor exploration dead-end (silent everywhere: look_around drops it, travel rejects it).
- why_broken: content/worlds/baldurs-gate/areas/bloomridge-market.json:9 lists "the Cloistered Quarter"; no such area/region exists among the 14 BG files (sole grep hit is the hint itself); seed-time name-resolution leaves unmatched names verbatim with no warning (content.py:1695-1701); travel.reachable silently drops unknown ids (travel.py:41-44); load_world_areas continues on JSONDecodeError/OSError with no diagnostic (content.py:684-688 — the one seeder violating the "[content] skipping" convention).
- how_to_fix: author/repoint the connection (wiki-first per owner direction); seed-time unresolved-hint warning; standard skip diagnostic; CI graph-integrity walker over all worlds (red on bloomridge today).
- test_strategy: post-seed every BG connection resolves (currently 1 failure); corrupt-file fixture emits the diagnostic.
- effort: S ; confidence: high ; dup: new.

---

## 3. RANKED BACKLOG

### P0 — breaks an RRI gate today
| # | ID | Title | Gates | Effort | Conf | Dup |
|---|---|---|---|---|---|---|
| 1 | F11-1 | image_render gate structurally un-passable on VM release lane (score.json 404-only fallback fabricates the denominator; precedence-blocks the landed #762 mac-handoff source, which needs literally-zero 404s — impossible since the VM counts designed no-art 404s) | image_render hard-FAIL 0.0 -> RRI blocked today | M | high | new (re-scoped onto landed #762/#730) |

### P1 — materially moves sat->7 / story->4.3 / mech->4.5 / image_render / latency budget
Ordered by (scorecard delta x confidence / effort).

| # | ID | Title | Gates | Effort | Conf | Dup |
|---|---|---|---|---|---|---|
| 1 | SYN-01 | Dead-beat masking: 401 text chats as DM prose; empty/hidden rows; fallback_recovered stamp dead in all 3 QA runners | no_give_up + sat | M | high | enriches #757+#745 |
| 2 | F12-1 | Routine 200s timeout kills ~18% of healthy beats; retry reuses the same deadline | no_give_up + sat + latency | S | high | enriches #753 |
| 3 | F12-8 | timeout(1) undeclared coreutils dep — every beat dies rc=127 on stock Macs, masked | native_gate | S | high | new |
| 4 | F12-3 | play.sh cold open: no failure abort, no seating guard — dead cold open = "running" unplayable session | no_give_up + native_gate | M | high | new |
| 5 | F12-4 | play_party missing the model-independent heartbeat (#623 never reached its target lane) | cross_persona_sat | S | high | new (completes #623) |
| 6 | F12-5 | play_party has no soft clock-tick — party sessions can freeze at day-1 morning | behavioral clock + story | S | high | new |
| 7 | SYN-03 | Canon-roster surfaces unbounded: 180KB list, roster-as-error misses, MCP-cap errors, cold-open Read-paging | latency + no_give_up | S | high | new |
| 8 | F13-4 | scores_db has no latency columns — #753 budget has no ledger (skill already mandates them) | latency program | S-M | high | enriches #753 |
| 9 | F14-8 | _char bare raise — ~60 tools inherit a dead-end on any id slip (wasted beat or freehand) | latency + mech | S | high | new (absorbs F14-20) |
| 10 | F05-1 | Rule-of-three quest evolution unreachable — no tool sets evolves_to; skill-documented call TypeErrors | story_craft | S | high | new |
| 11 | F07-1 | Recap + FTS ledger contaminated by combat/system bookkeeping — every cold open recites bookkeeping | story_craft | S | high | new |
| 12 | F14-4 | Scheduled consequences structurally never fire (18 writes, 0 fires; both due() sites have 0 calls) | story_craft | S/M | high | new |
| 13 | F06-1 | 2 of 3 companion-creation paths seed NO arc/dossier — arc system inert on the dominant path (20/20 snapshots) | story_craft + sat | S | high | new |
| 14 | F06-3 | companion_advise (only companion surface in live use, 54 calls) ignores dossier, gauge, arc | story_craft + sat | S | high | new |
| 15 | F09-1 | itemcatalog.resolve() AttributeError on every unique-substring match (one-line fix) | mech + latency | S | high | new |
| 16 | F09-2 | buy_item charges unit price ONCE regardless of quantity (sell mirrors) | mech | S | high | new |
| 17 | F02-1 | pickup-origin PC seats flat 10/10/10/10/10/10 abilities | mech + sat | S | high | new |
| 18 | F02-4 | gear+purse seeded on only 2 of 5 seat paths (53 wild AC>=14-no-armor players) | mech state-integrity + sat | S | high | new |
| 19 | F02-2 | paladin/ranger slots from round-DOWN multiclass table — 0 slots at L1 | mech (SRD omission) | S | high | new |
| 20 | F01-1 | Multiattack parser overcounts "replace one attack"/", or it makes" wordings (13 creatures, ~+50% DPR) | mech | S | high | new |
| 21 | F03-2 | 13/14 damage cantrips never scale at L5/11/17 (structured field actively wrong; exclude Eldritch Blast) | mech | S | high | new |
| 22 | F03-3 | No ritual casting — Detect Magic (top utility cast, ~12 distinct) burns a L1 slot every time | mech + resources | S | high | new |
| 23 | F03-5 | OOC expiry of a save-ends marker strands its condition — Hold Person victim paralyzed FOREVER | zero_critical-grade harm | S | high | new |
| 24 | F01-5 | Sneak Attack invisible at the attack trigger (~half rogue damage missing) | mech | S | high | enriches #166 |
| 25 | F01-4 | Finesse absent — DEX-martials get wrong surfaced melee numbers all campaign | mech | S-M | high | new |
| 26 | F01-2 | spawn drops monster save proficiencies + ALL monster PB=2 (132/344 creatures; CR-derived PB fix) | mech | M | high | new |
| 27 | SYN-05 | Action economy per-tool not per-turn: cast+attack/double-cast legal; bonus-action spells burn the action | mech (rubric item) | M | high | new |
| 28 | F09-4 | Entire economy/item toolchain dark in real play (0 calls; 0/32 update_character compensations) | sat + mech adoption | S | high | enriches #602/#604 |
| 29 | F09-3 | 760/960 catalog items (all magic items) purchasable for 0 gp (cost:null -> 0.0) | mech + optimizer sat | M | high | new |
| 30 | F06-2 | Approval gauge — sole arc input — has no organic mutation path; likes/dislikes are dead fields | story_craft keystone | M | high | enriches #593+#612/#613 |
| 31 | F04-2 | Production soft-tick silently discards world beats/developments/expiries (status="fired", zero readers -> permanent loss) | story + invariant 4 | M | high | new (#749=contamination; this=loss) |
| 32 | SYN-04 | All authored events manual -> full prose rides every beat (~1.6K tok) + minute-one pacing harm | latency + story pacing | M | high | new |
| 33 | F07-7 | Entire retrieval surface organically DEAD (0 recall-family calls across 345 transcripts) | story/sat at depth | M | high | new |
| 34 | F12-6 | Director+Event advisories run ONLY in the scored QA lane — RRI certifies a richer DM than players get | score provenance | M | high | enriches #643 |
| 35 | F14-3 | persist_beat: chosen:null crash, bare KeyError, events-before-validation non-atomicity (burned real gate beats) | no_give_up + state | M | high | new; depends F14-8 |
| 36 | SYN-02 | alwaysLoad pins ~40-44K tok of tool schemas per beat (54% of the input floor) | latency #1 line item | M | high/med | enriches #753 |
| 37 | F04-1 | Region danger/creature tables never match ANY shipped content — wilderness ambush model inside BG streets | mech + story + latency | M | high | new |
| 38 | F08-1 | load_slot rolls back the snapshot but NOT session logs — discarded timeline stays canon (undone TPK persists) | state-integrity + story | M | high | new |
| 39 | F10-1 | lookup_lore buries the dedicated lore page for natural queries (1-char tokens; tier-0 noise floor) | story_craft | M | high | new (#606=viewer) |
| 40 | F11-2 | image_probe_ok vacuously satisfiable (truthy on unservable null placeholder; scene-scope only) | image_render evidence | S | high | new; land with F11-1 |
| 41 | SYN-06 | Bless/Shield of Faith/Shield/Bane tracked but mechanically inert (no generic modifier fields on ActiveEffect) | mech | M-L | high | new |
| 42 | F09-5 | equip_item 100% cosmetic — AC/attack never change (stage-1 advisory ships S; stage-2 needs two-writer decision) | mech | S(v1)/L(v2) | high | new (#272/#605 viewer-only) |

### P2 — real defects, post-Beta (clusters; full specs in the per-unit sections, Part C)
| Cluster | Findings | Theme |
|---|---|---|
| Combat depth & enforcement | F01-7, F01-8, F01-9, F01-10, F01-11, F01-12, F01-13, F01-14 | grapple/shove/stabilize bypass every gate; grappled/restrained don't gate movement; concentration + death saves left to the DM; wandering spawns lose Parry; no mid-fight reinforcements; legendary actions unmodeled (31 bosses); outside-initiative loophole invisible to QA |
| Character progression integrity | F02-3, F02-5, F02-6, F02-7, F02-8, F02-10, F02-11, F02-12, F02-14, F02-15, F02-18 | missed-ASI debt + inert feats; class-sig recompute refills spent hit dice; CON never retro-adjusts HP; multiclass deletes Pact Magic; pickup seats dead records; recruit keeps stub HP/clobbers AC; resource-table drift; reroll location/AC; XP-entitlement advisory; expertise inert; cross-path seat-census test net |
| Spellcasting correctness | F03-4, F03-6, F03-7, F03-8, F03-11 | no AoE/multi-target path (validate-before-spend); 4 of 5 concentration-end paths don't release held victims; case-sensitive learn/prepare + wholesale replace; prepared-caster gate is a union no-op; monster/NPC casters can't route through cast_spell (innate flag) |
| World-clock & rest seams | F04-3, F04-4, F04-5, F04-6, F04-7, F04-8 | no one-long-rest-per-24h guard; long rest clears neither temp HP nor degraded-path concentration; downtime(0) REWINDS the clock; add_location advance path skips expiry/strategic/wander; long_rest never ticks the world; wander picker not level-banded (CR-5 wraith vs L1s) |
| Story machinery reach | F05-2, F05-4, F05-5, F05-6, F05-7, F05-10 | evolution skipped on 2 of 3 completion verbs; resolved scene-debts re-surface forever; update_decision doesn't exist (director nudge names it); faction arcs evaluate only on 2 dark call sites; quest_stalled ignores the engine's own progress verbs; prelude binds Raphael/Withers/Emperor in 34% of real cold opens |
| Companion depth | F06-4, F06-5, F06-6, F06-7, F06-8, F06-10(+F06-11) | betrayal warnings silent for thresholds <= -40; camp/banter pillar unreachable + never rotates + starves pair banter; heal suggestion "cast None" case bug; recruit XP backfill (false WARN); combat participation unenforced/unobserved; CompanionQuestArc has no content loader (+ link-error once-only) |
| Memory & retrieval prerequisites | SYN-10, F07-4, F07-8 | recall_npc split-brain; single-session recap blind spot (incl. start_session.previously_on); ledger full DROP+reparse after every save (content-true digest + incremental indexing) |
| Persistence robustness | F08-2, F08-3, F08-4, F08-5 | check_*/world_tick save unconditionally -> live-pointer flips (#640 class); tolerant-load drops unknown keys then save destroys them (write-once backup); strict-parse-only enumerators hide tolerant-loadable live saves; one torn log line bricks recap/resume (reader tolerance + newline hygiene) |
| Token-mass bounded surfaces | SYN-08, SYN-09 | unbounded history re-parse/recap/durable/tail; echo-back returns (~110K tok corpus) |
| Economy correctness & affordances | F09-6, F09-7, F09-9, F09-10, F09-13 | armor dex-mod rules dropped (Breastplate wrong, Shield "AC 2"); owned items persist zero structured stats (engine root of #756); sell has no price sanity; adjust_currency can't make change (spend_gp/earn_gp); table-driven economy stress suite |
| Social/NPC data integrity | SYN-07, F10-4, F10-6, F10-7 | parley/actor binding; seed writes roster ROLE into attitude (1,520 records); two unreconciled attitude tracks + label wipe; __notoc__ in 515 canon backstories (case-insensitive strip; enriches #758) |
| Image provider-lane hardening | F11-3, F11-4, F11-5, F11-6, F11-7 | fire-and-forget art lost on claude -p exit (detached resolver); media-dir watcher cross-attribution; cache-hit returns multi-MB bytes_b64 into DM context; no scope/catalog idempotency (2,359-dir catalog unconsulted); failures completely silent (.error sidecar) |
| Wrapper/runner hardening | F12-2, F12-9, F12-10, F12-11, F12-12, F12-13 | sonnet cold-open margin; codex wrapper retry/timeout/stale provider_status/budgets; claude lanes never write provider_status; run_duo per-beat deadline + failure surfacing; party companion turns unbounded; play.sh idle ceiling + launch lock |
| API ergonomics & error surfaces | F14-2, F14-5, F14-7, F14-9, F14-10, F14-11, F14-13, F14-18 | bounded SnapshotSchemaError; travel_to BFS route hints; cast refusal omits known/slots; cold-open composite (6-9 serial calls); rules-server domain check FIRST; update_character in_party + readable validation errors; soft-error counter for the gate; voice/rules alwaysLoad |

### P3 — polish (clusters)
| Cluster | Findings |
|---|---|
| Combat polish | F01-15 (monster speed), F01-16 (saving_throw adv/disadv), F01-17 (remove-current-actor stale turn) |
| Character/spell polish | F02-9 (dup living player), F02-13 (down-level features), F02-16 (point_buy validation), F02-17 (model bounds), F03-10 (multiclass DC ability), F03-12 (components/costs), F03-13 (Shield reaction), F03-14 (slot-error affordance + pact recompute) |
| World/story polish | F04-9 (tick-cap stall), F04-10 (OA advisory 0-HP/allies), F04-11 (camp membership), F04-12 (dead-companion drag), F04-13 (watch keyword match), SYN-11 (Cloistered Quarter), F05-8 (director nudge gaps), F05-9 (debt flags dead NPCs), F06-9 (self-revive advice) |
| Memory/persistence polish | F07-6 (kind whitelist), F07-9 (OperationalError guard), F07-10 (temporal anchors), F07-13 (no-match signal), F08-6 (ghost beats), F08-7 (session remint orphans), F08-8 (lock-dir litter), F08-9 (validate-at-save) |
| Economy/NPC polish | F09-8 (encumbrance surface), F09-11 (pp/ep preservation in pay AND gain), F09-12 (plot/stolen flags + attune no-op), F10-8 (social_check dc=0), F10-9 (parley proficiency filter), F10-11 (lookup_lore double scan) |
| Pipeline/API polish | F11-8 (scope docstring), F12-16 (WORLDOS_* twins), F12-17 (seed-param rendering), F12-18 (companion-spec reuse), F12-19 (validate_attack), F12-20 (consolidation bundle), F12-21 (canon-hero fallback), F13-8 (cold-open card), F14-12 (learn/prepare add/remove), F14-15 (speak_lines), F14-19 (lookup_item cross-links) |

---

## 4. rc1/rc2 PERSONA COMPLAINT MAPPING (majors/criticals)

| Complaint (run) | Maps to |
|---|---|
| Silent character switch Rolan<->Liara/Florrick/Zevlor x6 (rc1 adversarial/narrative/newbie x2; rc2 narrative x3, newbie) | #640 + SYN-07 (F12-15 facade re-resolution) + F08-2 (check_*/world_tick saves flip the live pointer) + F08-4 |
| Player action silently dropped, no DM response (rc2 narrative) | SYN-01 + F12-1 |
| 2+ min blank-spinner opening beats (rc2 newbie) | #753/#748 + F12-1/F12-2 + SYN-02/SYN-03 + F14-9 + F13-8 |
| First session dropped mid-compose and wiped everything (rc2 newbie) | #745 (+ SYN-01 visible-failure leg) |
| Parley NPC switches mid-interaction (rc1 adversarial) | #751 + SYN-07 engine anchor |
| Opening narration duplicated in Chronicle (rc1 adversarial) | #502 (existing) |
| Initiative != DEX modifier (rc1 optimizer) | #733 — FIXED at HEAD (unit-2 clean-verified) |
| Subclass free-text, no options/previews (rc1+rc2 optimizer) | #750 (in-flight) + #607 |
| Rest & Prepare display-only (rc1 optimizer) | #610 + F06-5/#592 + F14-12 |
| Item inspector shows no mechanical stats (rc1+rc2 optimizer) | #756 + F09-7 (engine root cause) |
| Feats untracked/unbrowsable (rc2 optimizer) | F02-3 + #345 |
| Bestiary omits Legendary Actions/Resistances (rc2 optimizer) | F01-13 + #758 |
| Action-economy display absent (rc1 veteran) | SYN-05 (engine state) + #247/#595/#596 (viewer) |
| No visual combat grid; invisible dice results (rc2 veteran) | #318/#461/#585; #608/#596 |
| Spell inspection one-liners (rc2 veteran) | #345/#308 + #754 |

**ORPHANED complaints (no finding, no open issue found — coverage gaps; all viewer-layer):**
1. **Chronicle log truncates mid-word/mid-sentence at a fixed ceiling** — rc2 adversarial + narrative (and rc1 veteran's "full DM narration should be readable"). Three personas, recurring. File a viewer issue.
2. **XSS partial sanitization: `<script>` stripped but inner text leaks into the chronicle as a player action** (rc1 adversarial). Security-adjacent input-sanitization gap.
3. **Battle Log leaks internal metadata labels ("narration", "dialogue", "began narration") into visible text** (rc1 adversarial) — #749/#763 fixed heartbeat contamination only; label rendering unowned.
4. **Inline character-sheet button always dead** (rc2 adversarial) — closest issue #598 covers combat tiles only.
5. **Navigating away mid-narration corrupts session state + permanently freezes the scene image** (rc2 adversarial) — adjacent to #745/#648 but a distinct trigger; verify #745's fix covers it or file separately.

---

## 5. EXPLICITLY NOT-CRITICAL (found suboptimal; safely deferrable)

- F09-8 encumbrance inert — SRD *variant* rule, zero observed demand, TELL-only fix (verifier downgraded P2->P3).
- F02-9 second-living-player guard — zero wild occurrences; QA seats once; robustness backstop only.
- F03-10 multiclass casting-ability pick — zero multiclass characters in any QA play-state; field impact nil today.
- F03-13 Shield reaction seam — house-rule opt-in; meaningless until SYN-06 lands.
- F07-9 recall OperationalError guard — sub-ms window, zero consumers, zero organic callers.
- F08-6 ghost beats — exception-window only; accept-and-bound (AST lint) is sufficient.
- F08-8 lock-dir litter — cosmetic; fix only via lock relocation (never prune in a read path).
- F11-8 generate_image docstring — 162/162 real calls used correct scopes anyway (the skill wins).
- F12-16 WORLDOS_* env twins — viewer legacy fallback verified working; #295 forward-contract only.
- F12-21 canon-hero silent fallback — needs an owner taste call (fail-loud vs substitute+notice) before work.
- F13-8 cold-open skill-file reads — distillation risks the story north star; ship only behind a story_overall A/B.
- F14-10 rules-server adoption — domain check FIRST (score one transcript vs SRD); demote the SKILL rule if accuracy holds.
- F14-15 voice speak batching; F14-19 lookup_item docstring cross-links; F01-15 monster speed (load-bearing only if #461 lands); F02-16/F02-17 input-validation backstops.

---

## 6. CLEAN-VERIFIED SUMMARY (per unit — provably audited, not skipped)

- **U01 combat:** crit math; damage-pipeline order; R/I/V substring safety (re-measured 0 qualified entries); death-save math; monster-die-at-0 vs PC-dying; condition->roll plumbing; shared save-bending helper; concentration lifecycle; attack-side economy; initiative (#733 fixed); next_turn integrity; effect expiry; zones advisory; Parry on spawn_monster; Guiding Bolt rider; maneuver damage; encounter math; XP; end_combat advisory; temp-HP/set_hp; dice.py; surprise = documented design choice; Mage Armor AC.
- **U02 character:** ASI shape+20-cap; update_character id/forbid/aliases/deep-merge; #733 DEX->init; #352 floor; canon derivation+warning; #305 gate; dup-companion guard; class-resource/slot used-preservation; PB-by-total; level_up HP/hit-dice sync; multiclass prereqs+house-rules; #624 subclass backfill; gen_ability_scores; reroll core swap; lock+atomic save on all unit-2 mutators. (Adjacent, correctly unfiled: preview_level_up:5038 subclass omission -> #750; leveled spellbook -> #754.)
- **U03 spells:** slot-spend rejections all precede state change; curated upcast/cantrip scaling math; single-concentration replacement at cast; damage->conc-DC pipeline incl. temp-HP and 0-HP clears; reaction/turn gating spends nothing on rejection; graceful degrade on un-curated spells; parse_duration; repeat-save rider + #209 loop; on-hit rider (#186); Mage Armor end-to-end; rest interactions; use_resource; spell_save_dc formula; QA's 8 "doesn't know" rejections were legitimate.
- **U04 world:** travel graph honesty + advance_clock math + BG 27/27 graph integrity (sole gap = SYN-11); rest gating/restoration scope + morning convergence (RAW-correct, tested); advance_time combat guard + soft-tick combat skip (both sides); consequences/thread separation + tick idempotence/cadence; _stage_wandering_encounter contract (flag-gated, never auto-starts); zones tools incl. OA-never-auto-rolled; world_tick discloses every mutation in its own return (the DM-is-told breach is the CALLER — F04-2); set_pacing advisory-by-design; calendar display-only; _move_party_to/XP coherence.
- **U05 quests:** resolve_event idempotency short-circuit; trigger semantics incl. fall-direction + degrade; evolution-helper note-channel internals; milestone single-award latch on all three verbs; deterministic debt ids; read-only debt surfaces + sequential-never-nested scene_context locks; outcome/effect key parity; consequences.py discipline; faction-arc state machine; questgen degrade-not-abort; additive round-trips (152 snapshots); record_decision/persist_beat parity.
- **U06 companions:** arc machine engine-enforced under lock+save with exactly-once; snap-curve + M2 validator; PC-skip guard covers companions; suggest_action tactics/peril-override; recruit hygiene (guarded death-clear, None-guards, co-location); camp scheduler pure/deterministic + camp_scene lock-free read; quest-arc API validation; #739 _party_xp_recipients routing; social_check gauge semantics; scene_context sequential flock; #612-#616 scaffold-only confirmed; corpus census independently reproduced (2,903 events; advise 54 / recruit 38 / depth tools 0).
- **U07 memory:** heartbeat filters at all 3 seams (#749/#763 merged at HEAD); scene_context exec 5-25 ms + 17KB lean payload re-measured; SQL pre-rank kinds filter + world_state header; _safe_match injection safety; ledger never touches lock/snapshot; log_event lock discipline; read_log_all ordering; persist_beat batching; remember/forget semantics; scaffold leak 0.16%; independent 810-row census: 0 non-canonical kinds, 0 non-marker system rows.
- **U08 persistence:** 85/88 savers follow lock->load->mutate->save (3 unlocked write fresh ids only); start_character pre-lock c0 discarded and re-required; per-campaign cross-process flock; _atomic_write temp+flush+fsync+replace; pure reads save-free (only check_* write = F08-2); deterministic active-campaign tie-break; saves ~1 ms @130KB (refutes async/batched-save proposals + #17 urgency); dump->load->dump byte-identical; mutable defaults via default_factory; slot snapshot mechanics solid; safe_path_segment guards ids AND slot names; player_server strictly read-only; engine_sha/schema_version never-fatal; end_session early-return + XP-parity backstop. 23-assertion skeptic probe reproduces.
- **U09 economy:** copper math value-exact + negative guards + insufficient-funds; attunement limit/split; stacking identity; catalog first-wins/H1/FK-join; structured lookup; find clamp; no-partial-persistence (fresh-load, save-at-end, atomic, locked); encumbrance arithmetic; downtime clamp. Annotations: _split_one must copy F09-7's new fields; live lru-cache recs must be COPIED at grant.
- **U10 npcs:** adjust_attitude clamp; reputation/standing clamps; join_faction latch; faction-id discipline; social_check read-vs-influence + ephemeral-persists-nothing; met-gating/no spoiler leak (scene_context:8968); lookup_lore de-confliction stack; path containment; world/adventure loud-validation + degrade-not-abort; area dedupe + bidirectional wiring (0 dangling region edges); lore corpus hygiene (0/351 markup); get_prelude; load_canon gates/fuzzy/precedence; is_dead_record guards; find_npcs tri-state.
- **U11 images:** async fast-path shape; degraded-results-not-cached; null-placeholder key isolation (provider in hash); /image containment+b64+302 hardening; _inflight double-spawn guard; sole-writer (derived cache only); tests exist (test_imagegen, test_openclaw_image); degrade chain, typed gateway errors, portrait_prompt, re-key chain, upload hardening consistent with all code read.
- **U12 pipeline:** single-flight lock impl+test; bash-3.2 discipline; %s%N real ns; effort/timeout/lean keyed off one `first` signal; remint gating (#719) read-only; record_dm_reply idempotency + heartbeat dedup-exemption + (new) block-breaking fallback; soft_tick combat guard; over_budget math + opus budget defaults; player_server security boundary; duo root/IS_SANDBOX loud-fail; file-based cursors + trust boundary; supervisors wait-and-reap + INT/TERM cleanup; codex input hygiene + frozen wire contract + atomic status writes; worldos_env sentinel; duo qa.mcp re-rooting + alwaysLoad pin.
- **U13 latency:** engine hot path innocent (bench reproduced byte-exact; ~1-4%/beat stands); alwaysLoad #574 wired x3; effort tiering #551 in all 3 loops; lean-ON is the production+QA default (the latency-forensics SKILL's "lean BROKEN/OFF" text is stale doc-drift — flagged); combat returns lean; wall-clock trend right direction; non-API outliers = rate-limit backoff; scores_db ALTER path additive.
- **U14 api:** no traceback leakage; #716 aliases; social_check companion-target FIXED; update_character recompute + #733; persist_beat adoption/sequential-advance; travel_to write path; attack guards mirrored in cast_spell; store atomic+stamped; scene_context lock/durable-first; rules impl clean (adoption = F14-10); lookup_item suggestions; cast_spell SRD degrade; 141 tools no dupes (live-counted); voice degrade contract; sweep archives clean; rests side-flag: ZERO long/short_rest calls in 345 transcripts (see coverage gaps).

---

## 7. COVERAGE GAPS (zero-finding zones and under-audited areas)

1. **Viewer/OpenWorlds GUI layer** — deliberately out of engine-audit scope, but 5 of the rc major complaint classes (Section 4 orphans) live there: chronicle truncation, XSS inner-text leak, metadata-label leak, dead sheet button, navigate-away corruption. These need filing; the engine audit cannot certify them fixed.
2. **Rest/downtime DM adoption** — ZERO long_rest/short_rest calls across 345 transcripts (U14 census). The engine rest MECHANICS are audited clean (U04), but no unit owned the adoption gap and no behavioral check counts rests; pairs with the camp pillar (F06-5/#592) and the F09-4 adoption pattern.
3. **Voice server** — only F14-15 (batching) + the clean-verified never-raise degrade contract; no dedicated unit pass on voice_id mapping breadth/quality. Low risk; noted so the zero is explicit.
4. **Support-VM concurrent sweep lane** — single-process invariants and cross-process flock verified (U08), but the engine was not stress-audited under concurrent multi-persona sweep load.
5. **Bestiary data quality beyond stat blocks** — owned by in-flight #756/#758; this audit verified the engine consumption side only (F01-2 PB/saves, F01-13 census, F09-6 flatten).

No unit returned zero findings; every clean area cites its checklist line in Section 6.


---

## 8. LATENCY BASELINE TABLES (unit 13, verbatim — re-verified byte-exact at HEAD a245a2c)

## DELIVERABLE A — Latency BASELINE tables (percentiles, real transcripts)

### A1. Mac archives — qa/transcripts/*.dm.*.jsonl + play-state/*/dm.*.jsonl (269 files, 253 result events)
Era: 2026-05-28 → 05-31 = PRE-#574 (no alwaysLoad), PRE-#551 (no lean default, no effort tiering), full `--resume`, Sonnet 4.6.
Parser: /tmp/engine-audit/parse_beats.py (cold-open = beat containing a `start_world` tool_use). Raw rows: /tmp/engine-audit/beats.json.

| group | n | metric | p10 | p50 | p90 | p99 | max |
|---|---|---|---|---|---|---|---|
| routine, sonnet | 214 | duration_api_ms | 58,562 | **146,573** | 221,135 | 333,307 | 356,316 |
| | | duration_ms | 60,804 | 150,796 | 224,015 | 336,300 | 359,540 |
| | | num_turns | 2 | 5 | 11 | 16 | 27 |
| | | output_tokens | 2,100 | 6,080 | 9,147 | 14,506 | 16,577 |
| | | cache_read (sum/beat) | 122,043 | 296,704 | 556,431 | 969,137 | 1,725,110 |
| | | cache_creation (sum/beat) | 4,283 | 11,122 | 91,226 | 142,030 | 153,767 |
| | | **non-API share %** | — | **2.0** | **5.7** | — | 52.4* |
| cold-open, sonnet | 36 | duration_api_ms | 189,688 | **258,852** | 307,814 | 349,495 | 364,493 |
| | | num_turns | 18 | 22 | 29 | 34 | 34 |
| | | output_tokens | 8,843 | 11,700 | 14,550 | — | 15,809 |
| | | non-API share % | — | 2.0 | 3.0 | — | 4.0 |

\* The 52.4% outlier (play-state/sweep-newbie-b/dm.1780181956697949000.jsonl) contains a `rate_limit_event` — the 81.8s gap is **API rate-limit backoff, not engine tool wait**. Next-largest non-API gaps are 7–10s on 19–25-turn beats (≈0.4s/round-trip incl. MCP).

Final per-request input context (last assistant `usage`, n=265): p10=61,771 p50=**96,167** p90=**145,992** max=166,150 tokens — full-resume context grows toward the 200K window.

### A2. VM sweep data — root@178.104.123.213:/root/worldos-qa/WorldOS/play-state (148 files, 130 results)
Era: 2026-06-02+ = POST-#574 (alwaysLoad) + POST-#551 (lean-ON default + effort max-coldopen/medium-routine). Fetched via read-only ssh (parser inline; rows in /tmp/engine-audit/vm_beats.json, ctx in vm_ctx.json).

| group | n | metric | p10 | p50 | p90 | max |
|---|---|---|---|---|---|---|
| routine, sonnet | 82 | duration_api_ms | 47,972 | **80,446** | 118,271 | 285,829 |
| | | num_turns | 3 | 4 | 8 | 23 |
| | | output_tokens | 1,799 | 3,235 | 5,002 | 12,703 |
| | | cache_read (sum/beat) | 216,323 | 296,202 | 384,235 | 1,054,397 |
| | | cache_creation (sum/beat) | 12,949 | 15,070 | 20,746 | 116,507 |
| | | ToolSearch/beat | 0 | **0** | 0 | 4 |
| cold-open, sonnet | 31 | duration_api_ms | 142,580 | **173,718** | 239,276 | 383,057 |
| | | num_turns | 15 | 22 | 25 | 32 |
| routine, opus | 6 | duration_api_ms | 60,345 | **85,806** | 120,600 | 122,201 |
| cold-open, opus | 6 | duration_api_ms | 27,470 | 100,132 | 239,308 | 299,623 |

Per-request input context (VM, lean-ON):
- routine: **first request of the beat = 73,693 tokens p50** (p10 73,598 / p90 73,901 — a near-constant FLOOR), last request p50 80,177 / p90 85,843 / max 104,262.
- cold-open: first 73,455 p50; last p50 113,716 / p90 122,745 / max 142,598.

**Era-over-era (routine p50): 146.6s → 80.4s api (−45%)**; cold-open 258.9s → 173.7s (−33%). The #551+#574 levers measurably worked. Caveat: VM rows sometimes show duration_api_ms slightly > duration_ms (parallel turn segments are summed) — clamp non-API share at 0.

### A3. Beat-loss census (wasted whole beats)
- Mac: 16/269 files have NO result event (crashed/truncated beat) + 3 beats are 1.1–2.8s `is_error:true, api_error_status:401` instant-fails ("Failed to authenticate") recorded as subtype "success".
- VM: 18/148 no-result + 5 instant ~0.5s error beats.
- Total: **~42/417 invocations (≈10%) produced no usable DM beat** — each is a full player-visible wait + retry. One `error_max_budget_usd` kill at 232.9s/7 turns (ow-rv2) and one at 159.0s (vm2-opuslean-narr-b).

## DELIVERABLE B — Engine hot-path micro-bench (Task 2)
`CLAWDND_STATE_DIR=$(mktemp -d) uv run --directory servers/engine python /tmp/engine-audit/bench.py`, real baldurs-gate content (2,459 files, 10 MB). Script: /tmp/engine-audit/bench.py.

| call | ms | return bytes (~tokens) |
|---|---|---|
| import server (module load) | 432.5 | — |
| start_world COLD (content load) | **90.1** | 10,864 (~2.7K) |
| start_world WARM (2nd campaign) | 23.6 | 11,177 |
| get_state | 1.5 | 626 |
| look_around | 2.0 | 1,787 |
| scene_context DEFAULT | 3.6 | 8,145 (~2.0K) |
| scene_context recent_narration=8 | 3.4 | 8,169 |
| scene_context narration=8 + recall | 11.3 | 9,881 (~2.5K) |
| recall(query) | 1.6 | 2,021 |
| lookup_lore | 57.1 | 4,559 |
| list_canon_characters | 161.6 | **180,630 (~45K)** |
| list_canon_characters playable_only | 98.4 | **173,199 (~43K)** |
| load_canon_character (hit) | 61.9 | 271 |
| load_canon_character (MISS) | 105.0 | **28,187 (~7K)** |
| store.save_campaign ×50 | p50 0.58 / p90 1.23 / max 2.05 | — |
| start_combat (2 combatants) | 2.1 | 451 |
| attack (1d8+3) | 1.5 | 602 |
| next_turn | 1.3 | 908 |
| end_combat | 1.5 | 17 |

**VERDICT on "engine is 1–4% of a beat": CONFIRMED.** Worst engine call = 161ms; a whole 5-tool beat costs <0.3s of engine time vs 80–260s of generation. Mac-era non-API share p50 2.0%/p90 5.7% (p90 inflated by rate-limit backoff + pre-#574 ToolSearch, not engine); VM-era ≈0.1%. **start_world content load (90ms) is REFUTED as a cold-open contributor** — cold-open is ~22 turns of generation.

## DELIVERABLE C — Token-mass audit of per-beat tool returns (Task 3)
From 269 Mac transcripts (parser /tmp/engine-audit/tool_mass.py; sizes /tmp/engine-audit/tool_sizes.json). Top returns by TOTAL mass observed:

| rank | tool | calls | p50 B | p90 B | max B | total MB | note |
|---|---|---|---|---|---|---|---|
| 1 | Read | 113 | 9,833 | 16,567 | 19,858 | 1.18 | ~80 = skill reference re-reads at cold-open; ~16 = paging OFFLOADED list_canon_characters results |
| 2 | start_world | 41 | 12,817 | 14,883 | 15,266 | 0.54 | once/session |
| 3 | log_event | 195 | 940 | 2,775 | 6,246 | 0.25 | MOST-CALLED tool; echoes the full entry back |
| 4 | get_quest_hooks | 34 | 6,920 | 6,959 | 6,992 | 0.22 | once/session |
| 5 | scene_context | 16 | 9,686 | 13,357 | 14,407 | 0.17 | the lean spine — by design |
| 6 | companion_advise | 30 | 4,129 | 6,394 | 10,263 | 0.13 | full personality dossier each call |
| 7 | load_canon_character | 63 | 357 | 412 | 42,767 | 0.11 | max = MISS roster dump |
| 8 | look_around | 46 | 2,409 | 2,409 | 2,560 | 0.10 | fine |
| 9 | persist_beat | 32 | 2,322 | 5,916 | 11,529 | 0.09 | |
| 10 | remember | 104 | 600 | 979 | 1,521 | 0.07 | fine |

Combat returns are all lean (attack p50 457B, next_turn 1,319B, start_combat 1,939B) — clean.

**The dominant per-beat input mass is NOT tool returns — it is the fixed prompt floor:**
- Engine MCP tool-list JSON (141 tools, names+descriptions+inputSchema): **159,936 B ≈ 40K tokens** (measured via `server.mcp.list_tools()`); docstrings alone are 89,713 B (~22.4K tokens). rules = 11 tools (~0.3K), voice = 4 (~0.2K).
- With `alwaysLoad: true` (play.sh:115, play_party.sh:161, run_duo.sh:108-109) this entire surface is pinned into EVERY request → it is ~40K of the measured 73.7K-token lean first-request floor (~54%).
- DM brief qa/play_dm_duo.txt 4.6KB; lean re-ground directive ~3.6KB (~0.9K tok); dungeon-master SKILL.md 38.5KB + reference/ 85KB (storycraft.md 17.3KB and quest-generation.md 9.5KB are Read every cold-open: 40 and 39 reads vs 41 cold-opens).

## DELIVERABLE D — Lean path reality (Task 4)
Measured, lean-ON production config (VM) vs full `--resume` (Mac era):
- Full-resume final-request ctx: p50 96.2K, p90 146.0K, max 166.2K tokens (grows unbounded with campaign length).
- Lean per-beat ctx: first request 73.7K (constant floor), last request p50 80.2K, p90 85.8K, max 104.3K.
- **Real per-request delta: ~16K tokens (−17%) at p50; −41% at p90.** The advertised "~10–27× context drop" describes replayed-transcript tokens, not request input — the fixed 74K floor (54% tool schemas) dominates. Lean's true win = it CAPS late-campaign growth (resume marches toward 166K+; lean stays ≤104K) and bounds cache_creation; wall-clock −45% routine p50 came from lean+alwaysLoad+effort-medium combined.
- What lean actually sends per beat: fresh session (uuid --session-id) + `--append-system-prompt` re-ground directive (lib_beat_driver.sh:408-428) + first tool call scene_context(recent_narration=8) ≈ 9.7KB p50 return. Confirmed identical helper shared by play.sh / play_party.sh / run_duo.sh (no harness drift).

## DELIVERABLE E — scores_db latency columns (Task 5 spec)
qa/scores_db.py COLUMNS (L80-110) has NO latency fields today; schema is explicitly additive (`_ensure_schema` ALTER-adds new columns, L142-149). Spec in F13-4 below.

---

# PART C — PER-FINDING DEEP SECTIONS (verified unit reports, full schema fields, implementation-ready)


════════════════════════════════════════════════════════════════
## UNIT 01 — VERIFIED REPORT (verbatim from unit-01-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 1 — COMBAT CORE: SKEPTIC-VERIFIED FINDINGS
Verified 2026-06-11 against /Users/lume/ClawDnD-val. Audit SHA f24a102; repo HEAD a245a2c (3 commits ahead — diff touches ONLY level_up/subclass #750 + narration-heartbeat #749 in server.py; combat.py / bestiary.py / dice.py / encounter.py / models.py / spells.py byte-identical). All audit line citations re-opened on HEAD and hold (combat-region line numbers unshifted). Every measurement claim re-run from scratch. Dups re-checked vs /tmp/engine-audit/open-issues.txt (146 issues incl. #748–#758): no collisions.

**Verdict: 15 confirmed as written, 2 confirmed-with-corrections (F1-1 measurement understated + spec flaw; F1-2 fix spec INVALID as written — new companion defect found), 0 refuted.**

---

## F1-1 [P1|high|S] Multiattack parser overcounts — CONFIRMED, measurement UNDERSTATED, fix spec CORRECTED
**Verification:** `_parse_multiattack_count` (server.py:2800–2828) re-read: splits on `and`/`,`, sums first number of every part containing "attack(s)". `_parse_multiattack_composition` (2831–2862) same blindness. Flow into enforcement verified: 2944–2953 (parse at entry build) → `_attacker_multiattack_count` (2995–3009) → attack() gate (3914–3921) → `combat.attacks_allowed` (combat.py:113–126, raises per-action ceiling to multiattack) AND instruction surface ("Run N attack call(s)" note at 2965–2968; resolving `multiattack.sequence` at 2976–2991).
**Re-measured (full 344-block sweep, naive parser vs RAW-reference parser):** **13 creatures wrong, not 11** —
- Absolute Soul Seer 4→2, Barbed Devil 4→2 ("one Claws and one Tail, **or** two Hurl Flame" — both alternatives summed)
- Clay Golem 5→2 ("two Slam, **or** three Slam if Hasten")
- Medusa 6→3 (both alternatives summed; composition resolves all 6 names → a fully-resolving wrong 6-attack instruction sequence)
- Cloud Giant 3→2, Horned Devil 4→3, Infernal Inquisitor 4→3, Wight 3→2 ("replace one attack with…" +1)
- Werebear/Wereboar/Wererat/Weretiger/Werewolf 3→2 ("replace one attack with a Bite attack" +1)
The auditor missed Barbed Devil, Absolute Soul Seer, Cloud Giant, Horned Devil, Infernal Inquisitor, Wight. NOTE: all 20 adult/ancient dragons parse CORRECTLY by accident (their "It can replace one attack…" sentence has no comma → whole desc is one split-part → only the first number counts). Werewolf=3 reproduces the auditor's live probe (attacks [1,2,3] permitted) without needing a re-probe.
**Fix-spec CORRECTION:** sentence-split + drop "replace"/"instead of" sentences is right. But "split on ' or ' and take the FIRST alternative" is WRONG as written — bare `" or "` also appears INSIDE counting clauses ("makes two **Javelin or Morningstar** attacks" — Bugbear Stalker; "using Shortsword **or** Light Crossbow in any combination" — Assassin) and would zero/garble them. Split alternatives on the clause pattern `,?\s+or\s+(it|the \w+)\s+makes\b` (verified: corrects all 13, leaves all standard wordings byte-identical). Same pre-filter for composition (garbage names like "Javelin Or Morningstar" already degrade safely today because the composition only attaches when every name resolves — but the count does NOT degrade, hence the bug).
**Invariants:** pure parser change, no model/wire change. OK.
**Test:** red-first parametrized over the 13 captured descs (+ dragons/marilith unchanged); integration: Werewolf 3rd attack in one turn REJECTED.
**Dup:** new — nothing in open-issues owns multiattack parsing.

## F1-2 [P1|high|M] spawn_monster drops monster save proficiencies — root cause CONFIRMED, fix spec REFUTED-AND-REPLACED (deeper defect found)
**Verification:** bestiary `_saves_from_srd` (bestiary.py:291–306) returns `{short: TOTAL printed bonus}`; consumed only by intel/codex (tier-3 projection, bestiary.py:511–517). Neither spawn ctor (server.py:2015–2033, 2089–2107) sets `saving_throw_proficiencies`; `Character.saving_throw_bonus` (models.py:806–810) = bare mod + (prof if flagged). Re-measured: **132/344 with proficient saves — exact match**.
**Auditor's fix spec is WRONG on main today.** Claim "per-creature proficiency_bonus IS transferred… mod+prof reproduces the printed totals" is FALSE: re-measured, `sb["proficiency_bonus"] == 2` for **ALL 344 creatures** (raw srd524 `Creature.json` has `proficiency_bonus: null` everywhere → `int(f.get("proficiency_bonus") or 2)` fallback at bestiary.py:338/392). **229 of the save entries mismatch mod+sbPB.** Setting the flag list as specced gives Adult Gold Dragon (CR 17, sb PB=2) DEX save +4, not the printed +8 — the fix would ship a new wrong number.
**Companion defect (widened blast radius):** every spawned monster carries `proficiency_bonus=2` regardless of CR — also wrong for monster skill checks and for `combat.grapple_save_dc(attacker)` (8 + STR mod + prof) when a high-CR monster grapples.
**Corrected spec (two equivalent options, both invariant-clean):**
(a) Carry the printed totals: additive `Character.save_bonus_overrides: dict[str, int] = {}` consulted first in `saving_throw_bonus`; spawn factory sets it from `sb["saves"]`. Old snapshots round-trip (default {}).
(b) Fix PB at the source: derive PB from CR in `stat_block` when the raw field is null (PB = 2 + (⌈CR⌉−1)//4, standard table) — verified printed totals == mod + CR-derived-PB on Aboleth (all 4 saves) and Adult Gold Dragon (both); then the flag-list spec works AND grapple DCs/skills heal too. Prefer (b) + flags, with a sweep test asserting `saving_throw_bonus == printed total` for all 132 — any residual data quirk then forces (a)'s override for that creature.
Keep the shared `_monster_character_from_statblock` factory (fixes F1-11/F1-15 structurally).
**Test:** red-first Adult Gold Dragon `saving_throw_bonus(DEX)==8`; full-sweep equality test over all creatures with saves; no-saves creature → [].
**Dup:** new (#758 is stat-block UI polish — different).

## F1-3 [P1|high|M] Action economy is per-tool, not per-turn — CONFIRMED
**Verification:** attack() action-path gate (3910–3923) consults only `action_attacks_made` via `check_action_attack` — never `action_used` (and can't: use_action('action') at 3729 sets action_used=True before the blessed follow-up attack; attack() re-sets it at 4004). cast_spell gate (5305–5337) checks incapacitation + turn-ownership/reaction ONLY; sets `c.combat.action_used = True` unconditionally for an on-turn cast (verified ~5460 region: "An on-turn cast consumes the combatant's action") with **no casting_time branch** (5493 surfaces casting_time in the result but the economy write ignores it — Healing Word burns the action, as claimed). use_action('skip') (3713–3720) sets action_used; attack-after-skip passes because attack reads only attacks_made. `Combat` (models.py:1046–1060) records THAT, not WHAT — root cause exact.
**Tests:** test_action_economy.py = 4 tests, **0 mentions of cast_spell**; per-tool only. Confirmed zero cross-tool coverage.
**Fix spec invariant check:** additive `Combat.action_purpose: Literal[...]=""` — old snapshots deserialize to "" = today's behavior; rejection-only changes; engine-rolls untouched. PASSES. Bonus-action-spell edge (gate/consume `bonus_action_used` when casting_time has "bonus action") is correct 5e and additive.
**Dup:** new.

## F1-4 [P1|high|S–M] Finesse absent — surfaced melee numbers STR-only — CONFIRMED
**Verification:** `_combat_numbers` (2231–2249): `melee_attack_bonus = prof + str_mod` hardcoded (2243); the only "finesse" tokens in server.py are docstring/comment text not surfaced to the DM; models.py/combat.py have zero. `Item` (models.py:146–153) = name/qty/weight/equipped/attunement/description — no properties; inventory never inspected for attack numbers. Data exists: re-measured **exactly 6 finesse assignments** in data/srd/srd524/WeaponPropertyAssignment.json (dagger, dart, rapier, scimitar, shortsword, whip; property slug `srd-2024_finesse...`, lowercase — grep case-insensitively). Rogue starting gear seeds finesse weapons (the `_seed_starting_gear` docstring itself cites the level-3-rogue QA case).
**Fix spec:** read-surface only, pure cached helper, attack() keeps trusting the passed bonus — invariant-clean. Match on weapon slug names case-insensitively + substring-tolerant ("Rapier +1").
**Dup:** new (#166's validated case was STR-melee; DEX-melee never covered).

## F1-5 [P1|high|S] Sneak Attack invisible at the attack trigger — CONFIRMED
**Verification:** grep reproduced exactly — `sneak_attack` appears ONLY at models.py:687 (field) and sheet-write paths server.py:1175–1176, 4887–4888, 5044–5045. Zero reads in `_combat_numbers`, turn_brief, attack(). No once-per-turn tracking. The "never invent — use the sheet's" note (2247–2248) actively suppresses DM improvisation of it.
**Fix spec:** v1 surface-only (proven adherence channel), v2 additive `Combat.sneak_attack_used` — invariant-clean.
**Dup:** enriches #166 (class-feature coverage cluster) — correct.

## F1-6 [P1|high(root)/med(scope)|M–L] Tracked buffs have no mechanical teeth — CONFIRMED
**Verification:** `ActiveEffect` (models.py:472–537) re-read: fields are duration/concentration/grants_advantage/repeat_save/imposes_condition/armor_base_ac/armor_formula_ac — **no generic attack/save/AC modifier fields**. `_effective_armor_class` (3774–3804) name-matches ONLY "mage armor"; `_ADVANTAGE_GRANTING_SPELLS = {"Guiding Bolt"}` (combat.py:47); attack() reads effects only via adv_marker (3927); saving_throw reads conditions only. Bless/Shield of Faith/Bane are stored+ticked+ignored, and concentration effects live caster-side (5351–5363) so no target-side record. All confirmed.
**Fix spec invariant check:** additive fields default ""/0 → old snapshots round-trip; engine rolls the bonus dice and surfaces detail → engine-rolls respected; curated registry mirrors the existing `_ADVANTAGE_GRANTING_SPELLS` pattern. PASSES. Registry scope (which 4 spells) = med confidence, as flagged.
**Dup:** new (#618 is a viewer relay; #596 is viewer pips).

## F1-7 [P2|high|M] grapple/shove/escape_grapple/stabilize bypass every combat gate — CONFIRMED
**Verification:** grapple (5570–5640) re-read end-to-end: no `is_incapacitated(attacker)`, no turn-ownership/reaction gate, no economy writes — straight to DC + save resolution. Grep over shove (5644–)/escape_grapple (5719–)/stabilize (4485–): zero hits for is_incapacitated / combat.order / action_used / reaction_used / current_combatant. attack() and cast_spell both implement the pattern (3886–3923, 5305–5337) — the carve-out is real. test_grapple_shove.py (~20 tests) covers DC formula/save choice/immunity/idempotency ONLY — no gating tests. PC-skip guard (3497–3501) checks action_used/attacks_made/bonus only → a grapple-only PC turn trips "has not acted". All confirmed.
**Fix spec:** mirrors existing attack() gating; gates before rolls; inert outside combat — invariant-clean. 2024-RAW note: grapple/shove as Unarmed Strike options inside the Attack action (the module's own comment combat.py:657-region) — consuming one attack from the budget is correct.
**Dup:** new; RELATED #599 (GUI shove move-kind — player verb surface, not engine gating).

## F1-8 [P2|high|M] Grappled/Restrained don't gate zone movement; no Disengage state — CONFIRMED
**Verification:** move_to_zone (3222–3302) re-read: never reads `mover.conditions`; grappled mover moves with warnings=[] and the abandoned grappler IS listed as a provoker (3257–3269 lists all living cross-side sharers of from_zone — doubly wrong, the grappler can't OA the creature it's holding... actually the grapple broke illegally in the first place). No disengage state anywhere (use_action folds it into 'skip' which records nothing). Confirmed.
**Fix spec:** advisory-doctrine-preserving (`movement_illegal` note + provoker suppression + additive `disengaged` flag) — invariant-clean.
**Dup:** enriches #599 — correct (#599 adds GUI move-kinds incl. disengage; engine state is new).

## F1-9 [P2|high|M] Concentration check surfaced-but-ignorable — CONFIRMED
**Verification:** `_apply_total_to_hp` computes DC (combat.py:339–346: `max(10, damage_taken // 2)`, temp-HP-proof); attack() lifts it to result top as a cue (4179–4191, "(A4)"); `concentration_save` (4373) is a separate DM-initiated tool. No auto-roll call site (grep). The #209 auto-roll pattern exists in next_turn (3516–3569) but concentration stayed on the cue channel. Confirmed.
**Fix spec:** auto-roll server-side at damage time, combat.py stays dice-free (it already is — resolve_death_save takes a pre-made roll), keep concentration_save as manual override — invariant-clean, strengthens engine-rolls.
**Dup:** new (#596 = viewer concentration-break warning pip — different layer).

## F1-10 [P2|high|S–M] Death saves skippable — CONFIRMED
**Verification:** `death_save_due` is surface-only (3634, 3664); `roll_death_save` (4446) DM-initiated; PC-skip guard explicitly exempts downed PCs (`outgoing_able` requires `previous.current_hp > 0`, 3489–3494) — the dying clock can silently stop forever. Auto-roll precedent lives in the same function (#209 block, 3516–3569). All confirmed.
**Fix spec:** auto-roll at the start of the dying PC's turn via combat.resolve_death_save, order before turn_brief, keep manual tool — invariant-clean (engine-rolls; 2024 timing correct).
**Dup:** new.

## F1-11 [P2|high|S] Wandering spawns lose Parry — CONFIRMED
**Verification:** visual diff of the two ctors: spawn_monster sets `parry=bestiary.parry_bonus(sb)` (2029); `_spawn_creature_chars` (2089–2107) omits it despite its docstring "Mirrors spawn_monster's construction EXACTLY" (2072–2073). Classic duplicated-constructor drift. (Also omits `location_id` in spawn_monster vs present in wander path — intentional; and BOTH omit saves/speed → F1-2/F1-15.)
**Dup:** new; folds into F1-2's factory.

## F1-12 [P2|high|M] No way to add a combatant to a running fight — CONFIRMED
**Verification:** start_combat raises when active ("combat already active; call end_combat first"); the ONLY `order.append` in server.py is the view builder (line 189); place_combatant raises for non-order members. And the bypass half: attack()'s whole gate block conditions on `attacker_cb is not None` (3886–3890) — a mid-fight spawn not in the order attacks with zero gates. Confirmed.
**Fix spec:** new tool, engine rolls initiative, mirrors remove_combatant's index math, no model change — invariant-clean.
**Dup:** new.

## F1-13 [P2|high(gap)/med(v2)|M–L] Legendary actions/resistance unmodeled — CONFIRMED
**Verification:** zero "legendary" tokens in server.py/combat.py (grep). Census re-run from scratch: action_type histogram **ACTION 854 / LEGENDARY_ACTION 85 / BONUS_ACTION 81 / REACTION 30; 31 creatures with legendary actions — exact match**. `_parse_attack_action` (2888) feeds the combat entry from ACTION entries only. Compounded by F1-2 (legendary saves also dropped + PB=2). Confirmed.
**Fix spec:** phased v1 surface / v2 class_resources pool — additive, invariant-clean; v2 design med-confidence as flagged.
**Dup:** new.

## F1-14 [P2|high|S] attack/cast outside combat = full effect, no nudge; invisible to QA — CONFIRMED
**Verification:** gate block conditioned on `c.combat.active` (3886–3888); no start_combat suggestion anywhere in attack/cast_spell (grep). QA blindness confirmed: qa/assert_behavioral.py combat-integrity checks all nest under `if tools.get("start_combat", 0) > 0:` (line 253). Confirmed.
**Fix spec:** advisory cue + QA-side WARN, never a block — invariant-clean (trap/hazard inertness preserved).
**Dup:** new.

## F1-15 [P3|high|S] Monster speed never transferred — CONFIRMED
**Verification:** both ctors omit `speed=`; `Character.speed: int = 30` (models.py:636); `sb["speed"]` dict exists (bestiary.py:264–273). Re-measured: **201/344 walk ≠ 30 — exact match** (probe-level: Adult Gold Dragon walk 40 vs spawned 30).
**Dup:** new; folds into F1-2 factory; #461 noted as future consumer.

## F1-16 [P3|high|S] saving_throw can't express advantage/disadvantage — CONFIRMED
**Verification:** signature `saving_throw(campaign_id, character_id, ability, dc)` (5546) — no adv/dis params; condition-derived disadvantage computed internally; attack() has the caller-merge pattern (3938–3940) to copy; dice.roll supports both (dice.py:42–48, cancel rule included). Confirmed; fully additive.
**Dup:** new.

## F1-17 [P3|high|S] remove_combatant of the CURRENT combatant hands the next a stale turn — CONFIRMED
**Verification:** remove_combatant (3749–3771) pops + adjusts index math only (`idx < turn_index` decrement, modulo) — never touches action_used/bonus_action_used/action_attacks_made/surge_actions and never refreshes the new current's reaction_used; the reset block exists only in next_turn (3586–3598). When idx == turn_index, the next combatant becomes current and INHERITS the spent economy (their first attack rejected if budget was consumed). Tests (test_combat.py:448, 460; test_qa_fixes:912; test_xp_kill_time) never remove the current actor with spent economy. Confirmed.
**Fix spec:** factor `_begin_turn(c)` out of next_turn — invariant-clean.
**Dup:** new.

---

## CLEAN-VERIFIED LIST — spot-audited, stands
Items 1–25 accepted. Independently re-verified by the skeptic:
- **#3 (R/I/V substring safety): re-measured from scratch — 0 qualified entries** ("nonmagical"/"silver"/"adamantine"/"magic") across all 344 stat blocks. The do-NOT-file call is correct for this dataset; re-check on any 2014-style ingest.
- **#10 (initiative #733 fixed on main):** consistent with the merged fix + worktree `issue-733-init-mod`; initiative_bonus is transferred at spawn (2024).
- **#23 (surprise = documented design choice):** confirmed in start_combat's docstring ("v2 idea: also apply the 5e 'surprised creatures skip round 1' rule").
- **#24 (incap gates on attack+cast only):** confirmed (cast_spell 5311–5313; grapple family has none → F1-7).
- **#20 (spawn transfers AC/HP/abilities/prof/initiative/R-I-V/condition-immunities):** confirmed at 2015–2033 — with the caveat that "prof" transfers a uniformly-wrong PB=2 (see F1-2 correction).

## SKEPTIC DELTAS vs the auditor's report
1. **F1-1:** wrong-count creature set is 13, not 11 (adds Barbed Devil, Absolute Soul Seer, Cloud Giant, Horned Devil, Infernal Inquisitor, Wight); dragons are accidentally-correct; fix spec's bare-" or " split corrected to the ", or it/the-X makes" clause pattern.
2. **F1-2:** fix spec as written would ship NEW wrong numbers — `sb["proficiency_bonus"]` is 2 for all 344 creatures (srd524 nulls), 229 save entries mismatch mod+sbPB. Corrected spec: CR-derived PB at the bestiary layer (verified to reproduce printed totals) + flags, or a printed-totals override map. Companion defect surfaced: monster grapple DCs and skill checks also use the wrong PB=2. Effort S→M.
3. No finding refuted; severities held (no P0 claims; P1s map to the explicit mechanical-gate emphasis items; no inflation detected).

════════════════════════════════════════════════════════════════
## UNIT 02 — VERIFIED REPORT (verbatim from unit-02-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 2 — CHARACTER SYSTEM — SKEPTIC-VERIFIED (engine @ a245a2c, verified 2026-06-11)

Verification method: HEAD confirmed = a245a2c (matches auditor's claimed commit). All 16 probes
re-executed from `/tmp/engine-audit/u02_probes.py` against a throwaway state dir — every probe
output reproduced byte-for-byte. Every cited file:line opened and re-read (server.py seat paths
1325-1902, load_canon 2350-2548, update_character 2551-2653, level_up cluster 4680-5194,
helpers 300-398/1100-1322; srd_tables.py 200-340; rests.py full; models.py 90-154/600-810/1161-1180;
content.py guards). Wild-snapshot scans re-run: **53** kind=player AC≥14-no-armor records (exact
match), **1** flat-10 classed player (May-31 Alfira, cha 10/init 0 WITH Studded Leather+Rapier —
the start_character fingerprint), Jun-9 Alfira cha 15 / AC 14 / inventory [] / 0 gp (load_canon
fingerprint). Data files checked: spell_slots.json multiclass rows 1-3 = [2]/[3]/[4,2]; rogue
sneak hints 9d6@17, 10d6@20 (none@19); dead rage_uses hints present in class_features.json and
read by nothing (grep: only srd_tables._RAGE_USES is consulted). Dedup re-checked against all 146
rows of /tmp/engine-audit/open-issues.txt.

**VERDICT: 14 confirmed as filed, 4 corrected (F2-3 severity, F2-7 fix-spec, F2-9 severity,
F2-12 met-clause), 0 refuted.** The auditor's executed-probe discipline held up: not one
behavioral claim failed re-execution. The corrections are in mitigation-awareness (two severity
inflations), one factually wrong fix-spec footnote (rests.py pact special case), and one
overstated consumer claim (met).

---

### F02-1 [P1|high|S] CONFIRMED — pickup-origin PC seats flat 10/10/10/10/10/10
- Re-verified: pickup branch server.py:1544-1573 copies identity but never `rec["abilities"]`
  nor calls `_derive_canon_abilities`; fresh build 1587 `AbilityScores(**(build["abilities"] or {}))`
  → flat; promote branch 1600 `if build["abilities"]:` only. Contrast load_canon 2458-2471
  (canon→derived→placeholder + initiative reset) + flat-10 warning 2526-2537. Probe
  `[veteran-flat10]` reproduced (all 10s, max_hp=34, init=0). Wild: exactly 1 flat-10 classed
  player (May-31 Alfira) with start_character's gear fingerprint vs Jun-9 load_canon Alfira
  cha 15/dex 14/init 2 — fingerprints distinguish the paths exactly as claimed.
- Severity check: P1 stands — a documented origin (one of the 4-item menu) produces a mech-gate-
  breaking sheet (+0 everywhere) with one wild occurrence.
- Fix-spec vs invariants: additive (explicit abilities arg wins, class-less origins unchanged),
  mirrors an existing pattern — OK as written. dup: new (no open issue owns it).

### F02-2 [P1|high|S] CONFIRMED — paladin/ranger slots from round-DOWN multiclass table
- Re-verified: `_recompute_spellcasting` server.py:331-347 routes ALL casters through
  `multiclass_slots`; `effective_caster_level` srd_tables.py:233 `level // 2`;
  `_seed_starting_spells` server.py:1317-1318 gates half-casters to L2+. Probes reproduced
  (`ranger-L1 {}`, `paladin-L3 {1:2}`, `paladin-L5 {1:3}`). spell_slots.json rows verified.
- SRD check: 5.2 paladin/ranger cast from L1 and multiclass-count half ROUNDED UP; with ceil the
  multiclass table reproduces the 2024 class tables at every level (L1→CL1→[2], L3→CL2→[3],
  L5→CL3→[4,2]). L3/L5 counts are wrong even under 2014 single-class tables. The engine's
  feature data is already 2024 (Weapon Mastery/Cunning Strike in class_features.json — seen in
  probe output), so the round-up is the consistent edition.
- Fix-spec OK (slots re-derive only on mutation; old snapshots round-trip). Note: the seed-gate
  drop touches `_seed_starting_spells` (the fixed L1 loadout), NOT #754's leveled-spellbook
  scope — coordinate, don't conflate. dup: new.

### F02-3 [**P2 corrected from P1**|high|M] CORRECTED — missed ASI silently dropped; feat cosmetic
- Root cause re-verified on main: server.py:4804-4814 validates shape only, no else-branch
  records the due choice; feat application is 4856-4858 (`notes += " | feat: X"`), nothing else;
  no pending-choice field on Character (models.py pending_* fields at 651/657 are combat riders,
  not choice ledgers); no feats table in data/srd/. Probe `[missed-asi]` reproduced.
- CORRECTION (severity + wording): "unrecoverable via any engine surface" is overstated —
  (a) `preview_level_up` returns `choice_requirements: [{"type":"asi_or_feat",...}]` (5010-5013)
  and `build_options` surfaces `choices.asi_required` (5075, 5165) BEFORE the call; (b) the
  level_up return carries `_asi_applied: None` (visible to the DM); (c) update_character can
  manually apply the missed bump afterward. What's true and stands: nothing RECORDS the debt
  after a skipped call, no surface ever offers it again, and the feat path is 100% inert with
  zero mitigation (feats_allowed defaults True, models.py:1169). That is a real mech gap but
  with a documented pre-call surface and a manual recovery path it is P2, not P1.
- Fix-spec vs invariants: `pending_choices: list[str]` additive default [] — round-trips; OK.
  depends_on: coordinate with #750 (same pending-choice surface). dup: new (#607/#308 = UI wiring).

### F02-4 [P1|high|S] CONFIRMED — gear+purse seeded on only 2 of 5 seat paths
- Re-verified: `_seed_starting_gear` (1257-1276, self-guarding on existing inventory AND any
  non-zero purse) called only at create_character:1409, start_character:1621/1665. load_canon
  (2472-2508) and recruit_companion (1740-1741) apply SRD defaults incl. AC but never seed.
  Wild scan re-run: 53 player records AC≥14 with no armor item (exact). Jun-9 Alfira AC 14 /
  inventory [] / 0 gp reproduced.
- Fix-spec OK: the function's own guards mean canon-supplied kits win — additive. dup: new
  (distinct from #756 item-stats UI / #754 spellbook).

### F02-5 [P2|high|M] CONFIRMED — class-sig recompute refills hit dice; head-class walked to TOTAL level
- Re-verified: server.py:2635-2637 passes `new_ch.total_level` as head class's level;
  `_apply_srd_class_defaults` 1136-1137 sets `hit_dice_remaining = level` unconditionally;
  features_through(cname, level) at 1156; `_recompute_level_scaled_stats` 1217-1234 skips
  hit_dice/max_hp/extra_attacks for multiclass and `min(prev, total)` keeps the refill.
  Probes `[subclass-patch-hitdice] 1→3` and `[mc-after-patch] 5d6 + Memorize Spell` reproduced.
- dup: new — #716/#738 are landed and addressed different recompute defects (down-level retier
  numbers); this is adjacent, not covered.

### F02-6 [P2|high|S-M] CONFIRMED — CON rises never retro-adjust HP; level-gain uses pre-ASI CON
- Re-verified: con/gain computed at 4817-4822, ASI applied at 4850-4854 (after); no retro term;
  update_character recompute fires only on class-sig change (2635), #733 DEX pattern (2648-2650)
  never extended to CON. Probes `[con-asi-hp] 34→40 gain=6` and `[con-patch-hp] 24→24` reproduced.
- Fix-spec vs invariants: delta-based (`max_hp += delta_mod × levels`) respects DM-authored HP
  bases and multiclass — better than a re-derive; OK as written. dup: new.

### F02-7 [P2|high(loss)/med(repr)|M] CORRECTED FIX-SPEC — multiclass deletes Pact Magic; pact `used` reset
- Finding re-verified: pact branch server.py:342-346 gated `len(class_levels)==1`, builds
  `SpellSlotLevel(..., used=0)`. Probes `[warlock-solo {2:(2,0)}]→[warlock-mc {1:(2,0)}]` and
  `[pact-used-reset]` reproduced. The LOSS and the used-reset are confirmed.
- CORRECTION (fix spec): the auditor's footnote "rests.py refills by slot dict (it does — no
  special case)" is **false**. rests.py:20-21 `_is_single_class_warlock` + rests.py:58-62
  restore ONLY the pact slot level on short rest, gated single-class ("multiclass Warlock pact
  recovery is not modeled", docstring 38-39). Consequences for the spec: (a) merging pact into
  `spell_slots` by SUMMING maxima on a slot-level collision would make short rest refill the
  whole merged entry — refunding LEVELED slots (wrong); (b) even after dropping the recompute
  gate, a multiclass warlock's pact slots stay unrecoverable on short rest unless rests.py's
  single-class gate is also dropped. Corrected spec: represent pact distinctly (additive
  `pact_slots` field, or a tagged entry rests can identify), drop BOTH len==1 gates
  (_recompute_spellcasting:343 and rests.py:58), preserve used via the existing min() pattern,
  and have short_rest restore only the pact pool. Effort stays M. dup: new.

### F02-8 [P2|high|S] CONFIRMED — pickup skips is_dead_record; promote keeps death state
- Re-verified: pickup checks only `is_playable` (1551; content.py:60-64 defaults True absent
  flag); #305 gate exists only on load_canon (2370-2377); promote branch 1596-1636 never clears
  dead/stable/death_saves (recruit's clear is 1749-1753, guarded current_hp>0);
  `is_dead_record` exists (content.py:134) and is conservative-by-design. Probe
  `[dead-promote-match] dead=True` reproduced. test_playable_alive.py covers load_canon only
  (grep: no pickup test).
- Severity check: P2 holds — potential facade deadlock (dead PC at seat), not occurring today
  (so not P0). Fix-spec OK (same error shape as #305 for play.sh fallback reuse). dup: new.

### F02-9 [**P3 corrected from P2**|high(behavior)|S] CORRECTED — no second-living-player guard
- Re-verified: no living-player uniqueness check in start_character (1585-1673) or
  create_character (1413-1419); companion dup-guard exists (1365-1380). Probe `[dup-PC]` two
  players reproduced. Facade party-order-first/no-dead-filter claim corroborated by reroll's
  own docstring (1824-1828).
- CORRECTION (severity): the auditor's own evidence — 0 wild occurrences across the full
  snapshot corpus, QA harness seats exactly once — and self-flagged "med" confidence on
  severity make this a robustness BACKSTOP. Per the gate definitions (P0 breaks a gate today),
  a never-observed double-seat is P3, not P2. Finding and fix-spec (living-player
  `already_present` short-circuit; dead PCs don't block) otherwise stand. dup: new.

### F02-10 [P2|high|S] CONFIRMED — recruit keeps stub HP at level>1; clobbers authored AC
- Re-verified: `_apply_srd_class_defaults` fills HP only at `max_hp <= 1` (1138-1142); #352
  floor lives inline in load_canon only (2494-2508); recruit's `set_base_ac=(armor_class <= 0)`
  (1741) tests the ARG, not the record (create/start compare against unarmored 10). Probes
  `[recruit-L5-hp] 8 / 5d10` (n.b. via docstring-documented behavior — but a documented footgun
  that yields an instant-kill combatant is a defect, the #352 precedent agrees) and
  `[recruit-ac-clobber] 13→16` reproduced. Fix-spec OK (floor + record-aware AC guard,
  explicit args win). depends_on F2-4. dup: new.

### F02-11 [P2|high(counts)/med(recharge)|S+M] CONFIRMED (with one nuance) — resource table drift
- Re-verified: srd_tables.py:322 second_wind `lambda lvl, cha: 1`; :327 wild_shape
  `0 if lvl >= 20 else (2 if lvl >= 2 else 0)`; rogue 10d6 hint at "20" not "19" (json checked;
  probe `[sneak-L19] 9d6` reproduced); recharge literal is short/long/none only
  (rests._restore_class_resources); dead rage hints confirmed in class_features.json
  (rage_uses 2@1, 5@15, 6@17 — contradicting _RAGE_USES 5@12 which matches SRD 5.2; grep:
  nothing reads the json hints).
- Nuance kept in record: the wild_shape L20 zero is a DELIBERATE 2014 comment ("Archdruid
  unlimited → not pooled"), not an oversight — but it's mixed-edition drift against the
  engine's 2024 feature tables, and under 5.2 the pool must persist at 4 uses; fix direction
  unchanged. SRD 5.2 targets verified: Second Wind 2/3/4 @ 1/4/10; Wild Shape 2/3/4 @ 2/6/17,
  kept at 20; sneak 10d6 @ 19. Land count fixes first; `long_regain_one_short` literal is
  additive. dup: new.

### F02-12 [P2|high|S] CORRECTED (met clause) — reroll PC: location None, met False, AC 16 over empty inventory
- Re-verified: reroll Character literal 1861-1872 omits location_id and met; 1876
  `set_base_ac=True` while reroll deliberately skips gear ("lost with the body" docstring);
  start_character's fresh build sets location_id (1651, with the "QA: was null" comment proving
  the null-location defect class is real for PCs) but not met. Probe `[reroll-loc-gear]`
  reproduced exactly.
- CORRECTION (met): the ONLY met-filtering consumer (server.py:8968) applies the met test to
  `kind == "npc"` records exclusively — a player/companion with met=False has NO behavioral
  effect today (model comment models.py:709-710 "Companions/players are implicitly met"
  describes that consumer convention). The met fix is consistency/hygiene (create_character
  sets it True at 1396), not a behavioral defect — keep it in the patch but don't sell it as
  one. location_id (scene-cast absence at the post-death moment) + AC-without-armor remain the
  substance; P2 stands on those. Durable-fix suggestion (`_finalize_party_seat` shared helper)
  endorsed — it's the antidote to this whole F2-1/4/8/9/10/12 family. dup: new.

### F02-13 [P3|high|S] CONFIRMED — down-level retier leaves features at the old tier
- Re-verified: `_recompute_level_scaled_stats` 1228-1234 resets only extra_attacks; features
  append-only at 1170-1172 and 4882-4884; no prune path exists. Probe `[downlevel-rogue]`
  reproduced (Uncanny Dodge/Evasion/Reliable Talent retained at L1, sneak correctly 1d6).
- Fix-spec OK: prune only SRD-known names (homebrew survives) — additive-safe. depends F2-5
  (same function). dup: new.

### F02-14 [P2|high(behavior)/med(product-call)|S] CONFIRMED — no XP-entitlement guard in xp mode
- Re-verified: level_up 4790-4814 checks class/prereqs/house-rules/ASI shape; zero references
  to `leveling_mode` or `ch.xp` anywhere in the function; `leveling_mode` exists and defaults
  "xp" (models.py:1672); level_for_xp is reporting-only in award_* (4721-4726, 4754).
- Spec check vs invariants: warn-don't-block + optional `house_rules.strict_xp_leveling` is the
  right shape (engine stays sole writer; milestone unaffected; mirrors end_combat advisory).
  dup: new — #696 is QA-gate/provider-parity alignment, not an engine guard; confirmed distinct.

### F02-15 [P2|high|M] CONFIRMED — Expertise/Fighting Style/Weapon Mastery inert at grant
- Re-verified: `skill_bonus` pays 2×PB only from `skill_expertise` (models.py:797-804); the
  ONLY write path is the update_character `expertise` alias (server.py:2620) — no engine grant
  path exists; feature application understands exactly two hints (extra_attacks,
  sneak_attack_dice — 1170-1176, 4882-4888). Every engine-built rogue's expertise-skill math is
  short by PB. Fix rides F2-3's ledger; the default-fill interim mirrors 1181-1192. depends
  F2-3. dup: new (#345/#308 are UI affordances; engine has no mechanism).

### F02-16 [P3|high|S] CONFIRMED — point_buy validates budget only; "any"-pool fill ignores class
- Re-verified: 4689-4696 iterates the mapping with score-range + budget checks only — no key
  validation, no six-key requirement (probe: `{"luck":15,"strength":15}` accepted, reproduced);
  "any" expansion 1189-1192 takes first-N of SKILL_ABILITIES order (probe: bard →
  acrobatics/animal_handling/arcana). Fix-spec OK (accept model aliases when validating). dup: new.

### F02-17 [P3|high|S] CONFIRMED — ClassLevel.level / AbilityScores unbounded
- Re-verified: models.py:140-143 no constraints; 97-103 plain ints; probe `[negative-level]`
  persisted `-3d6` via update_character, reproduced. Fix-spec correctly self-aware of the
  additive invariant (corpus scan → clamp-in-validator if wild out-of-range values exist,
  mirroring _clamp_vitals philosophy) — approved as written. dup: new.

### F02-18 [P2|high|M] CONFIRMED — no cross-path seat census test
- Re-verified: tests/ contains no test_seat_census.py; start_character appears only in
  test_codex_provider_wrapper/test_content/test_qa_fixes (path-local concerns);
  test_playable_alive covers load_canon only; test_canon_abilities/maxhp, test_reroll,
  test_progression are all single-path. 5 of this unit's findings (F2-1/4/8/9/10/12) are
  literally "one seat path missed a fix another got" — the census + xfail burn-down is the
  highest-leverage prevention. Invariant bundle (a)-(l) is well-specified; clause (g) should
  carve out reroll if the "gear lost with the body" design stands (assert the chosen F2-12 AC
  spec instead). dup: new.

---

## Adjacent-to-in-flight (verified, correctly NOT filed)
- preview_level_up:5038 `features_at` only — no subclass choice-level features, no #624-style
  backfill (level_up has both at 4868-4881). CONFIRMED preview/actual drift; correctly routed
  to #750's implementer (or standalone P3/S).
- Leveled-spellbook-at-chargen: confirmed #754's scope (open issue verified) — correctly dropped.
- #748-#758 sweep re-done against open-issues.txt: no overlap with any kept finding.

## Severity ledger
- No P0s claimed, correctly: nothing here breaks a release gate TODAY (F2-8's deadlock is
  potential, not occurring).
- Inflation corrected: F2-3 P1→P2 (pre-call choice surface + manual recovery exist),
  F2-9 P2→P3 (zero wild occurrences, auditor's own med confidence).
- Kept P1 (verified deserving): F2-1 (wild flat-10 PC on a documented origin), F2-2 (every
  half-caster under-SRD incl. 0 slots at L1), F2-4 (53 wild broke/armor-less party records).

## CLEAN-VERIFIED (re-checked subset marked ✓; remainder accepted on auditor's code-cites)
✓1 _validated_asi_choice shape (314-328) ✓2 ASI 20-cap (4854) ✓3 update_character id-immutability
+ alias translations + forbid (2587-2620) ✓4 #733 DEX→initiative (2648-2650) ✓5 #352 canon HP
floor precedence (2494-2508) ✓6 canon flat-10 derivation + warning (2458-2537) ✓7 #305 dead-canon
gate (2370-2377) + lore pass-through ✓8 dup-companion guard (1365-1380) + already_present
(2378-2391) ✓9 _recompute_class_resources used-preservation/merge/custom-carry (350-380)
✓10 multiclass leveled-slot used-preservation (338-341) ✓11 PB by total level (4860, 1214-1215)
✓12 level_up HP gain/hit-dice sync/remaining+1 (4843-4848) ✓13 multiclass prereqs + house-rule
gates (4796-4801, 4811-4812) — 14 XP family (award/recipients/idempotency/backstops) accepted
— 15 level_for_xp clamping accepted ✓16 subclass resolution + #624 late backfill (4824-4881)
— 17 skill normalization accepted (models.py:787) — 18 get_character projection accepted
✓19 generate_ability_scores standard_array/seeded-roll (4684-4708) ✓20 reroll core swap
guards/demotion (1838-1892) — 21 _derive_canon_abilities parsing accepted (1100-1102 tail read)
✓22 campaign_lock + save on every unit-2 mutator (verified on all tools read).

════════════════════════════════════════════════════════════════
## UNIT 03 — VERIFIED REPORT (verbatim from unit-03-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 3 — SPELLS — SKEPTIC-VERIFIED report (2026-06-10; re-verified 2026-06-11)

> Re-verification pass (2026-06-11, HEAD still a245a2c): independently re-read every load-bearing code site from the f24a102 snapshots (_effective_armor_class 3773-3804; saving_throw 5523-5544; _commit_expiry/expire_clock_effects/expire_short_rest_effects/end_repeat_save_effect combat.py:549-655; tick_round_effects repeat-save exemption 560-574; cast_spell gate+spend 5283-5336; action_used set 5418-5419 vs use_action checks 3723-3726; learn_spells/prepare_spells raw store 6234-6253; _recompute_spellcasting pact 341-346; inverse sweep matcher 3620-3621) and re-dumped Bless/Shield-of-Faith curated mechanics (prose-only kind:"buff" confirmed). All findings, corrections, severities, and dup statuses below stand unchanged. open-issues re-check: #748-#758 own none of these (#754 = viewer spellbook/chargen; #753 = latency budget definition; #617/#618 = player-verb relays; #596 = viewer UI).

Verifier: skeptic pass over /tmp/engine-audit/unit-03-audit.md. Repo /Users/lume/ClawDnD-val, READ-ONLY.
**Repo state note:** HEAD is `a245a2c` (6 commits ahead of the audited `f24a102`). Diff f24a102..HEAD touches server.py only in level_up/subclass-backfill (#750) and scene-narration (#749) — **zero spell-surface changes**, so every finding verified below holds on main RIGHT NOW (line numbers cited are f24a102's; everything below ~5097 shifts ~+22 at HEAD).

Method: every cited file:line re-read from `git show f24a102:` snapshots; every measured census re-run from the same data files; QA field evidence re-grepped; fix-specs checked against engine invariants (sole-writer, additive/_StrictModel round-trip, engine-rolls, frozen wire contracts); dups re-checked against /tmp/engine-audit/open-issues.txt.

**Tally: 9 confirmed, 5 corrected, 0 refuted.** No finding's root cause failed re-verification — but two severities were inflated, two fix-specs contained a load-bearing error (a sweep that doesn't match the proposed child effects; per-row errors after a slot spend violating the reject-before-state-change discipline), one impact claim was overstated, and one field measurement was double-counted.

## Measurement re-verification (all re-run)

| Claim | Auditor | Re-run | Verdict |
|---|---|---|---|
| SRD spells total | 339 | 339 | ✓ |
| rituals | 29 | 29 | ✓ |
| concentration | 132 | 132 | ✓ |
| cantrips / w-damage | 27 / 14 | 27 / 14 | ✓ (all plain NdM) |
| damage_roll/save/attack/union | 119/127/42/186 | identical | ✓ |
| casting_time histogram | 257/23/4/40/15 | action 257 / bonus 23 / reaction 4 / minute 40 / hour 15 | ✓ |
| material_cost None ×339 | yes | non-None = 0 | ✓ |
| shape_type 52 | yes | 52 | ✓ |
| Detect Magic cast 24× (#1) | 24 (18+6 lc) | 12 via `spell_name` key + 12 via `spell` alias/result-echo key = 24 raw occurrences; **the result field `"spell":` echoes each cast, so distinct casts ≈ 12, not 24** | CORRECTED number; headline (clear #1 cast — Mage Armor is next at ~13 raw / ~7 distinct) HOLDS |
| Hold Person 5× | 5 | 3 + 3 alias/echo ≈ 3–6 | ✓ order-of-magnitude |
| Rolan known ⊋ prepared | play-state line | confirmed: known 7 (incl. Fire Bolt/Shield/Light/Mage Hand) ⊋ prepared 4 | ✓ + exactly 8 "doesn't know or have" rejections in that play-state (matches Thunderwave/Misty Step ×4) |
| Creature.json 0 spellcasting | 330 creatures | 330, 0 spellcasting-ish traits | ✓ |
| bestiary.py zero spell support | yes | `grep -ic spell` = 0 | ✓ |
| `ritual` unread in server.py | one docstring hit | confirmed: single unrelated hit (narrative "ritual" at ~7282) | ✓ |
| spell tests ≈20 | 20 | test_spellcasting 17 + test_spell_automation 4 = 21 | ✓ |
| QA caster levels | (implied low) | mostly 1–4; **level 5 (Raphael) and level 10 (Devella Fountainhead) casters exist** → F3-2 reachable today | ✓ |
| multiclass casters in QA | (none claimed) | **NONE in any snapshot** → F3-10 impact currently zero | drives F3-10 downgrade |
| Eldritch Blast record | "verify at implementation" | present, damage_roll 1d10, higher_level = **beam-scaled, separate attack roll per beam** → generic N×tier scaling WOULD mislabel it; the exclusion is mandatory, not hypothetical | spec hardened |

---

### F3-1 [P1|high|M] **CONFIRMED — fix-spec CORRECTED.** Engine-tracked buffs (Shield of Faith +2 AC, Bless +1d4, Shield +5) are inert in the engine's own resolvers
- Verified: `_effective_armor_class` (server.py:3773-3804) special-cases only `"mage armor"`; saving_throw (5524-5544) and concentration_save (4372-4393) roll `1d20+bonus` with zero active-effect consultation; curated Bless/Shield-of-Faith are `kind:"buff"` prose-only (re-dumped data/srd/spells.json); ActiveEffect (models.py:472-538) carries no generic bonus fields (only armor_base_ac/armor_formula_ac + grants_advantage). Shield (srd: reaction, "1 round") registers a 1-round effect that nothing reads. attack() computes target AC engine-side (3941) with no DM override, and saving_throw has no bonus-dice param — the buffs are genuinely UNREPRESENTABLE, not merely unautomated. P1 stands (engine advertises the buff in `active_effect`, then authoritatively rolls without it).
- **SPEC ERROR found and corrected:** the auditor's Bless multi-target child effects ("expired by next_turn's inverse sweep… same linkage as repeat-save markers") would NOT be expired — both the next_turn inverse sweep (3620-3621 `if eff.repeat_save is None or not eff.source_id: continue`) and drop_concentration's freed loop (4427) match ONLY repeat-save markers. Corrected fix: (a) additive ActiveEffect fields `ac_bonus:int=0`, `roll_bonus_dice:str=""` + matching curated mechanics fields, copied at cast (next to Mage Armor's special-case, 5393-5395); `_effective_armor_class` sums ac_bonus; saving_throw/concentration_save/attack fold roll_bonus_dice (engine rolls, surfaces the d4); (b) Bless child effects on up to 3 targets carry `source_id` + a new `linked_to_concentration: bool = False` (additive), and BOTH the inverse sweep and drop_concentration's loop are extended to also release source-linked child effects when the caster's twin ends (clear the effect; no condition involved). Old snapshots round-trip (all defaults).
- test: SoF target's attack() `target_ac == base+2`; blessed save total includes a d4 component; concentration break expires the child effects via BOTH paths; old-snapshot round-trip.
- dup: new (no buff-wiring issue in open list).

### F3-2 [P1|high|S] **CONFIRMED** (borderline P1/P2) — 13/14 damage cantrips hand the DM unscaled level-1 dice at caster levels 5/11/17
- Verified: srd path copies `srd.damage_roll` verbatim into `base_damage` (5468) and the note (5473-5478) tells the DM to "resolve with the values above"; only curated Fire Bolt scales (resolve_effect, spells.py:83-90, keyed on ch.total_level). Census re-run: 27 cantrips / 14 with damage_roll, all plain `NdM`. Mitigant noted: `result["upcast"]` carries the correct scaling prose adjacent — but that's DM mental math, the structured field is wrong. Severity: kept P1 — level-5 and level-10 casters exist in QA play-states TODAY, and the engine actively misreports for them; acknowledge it is inert for the dominant level-1-4 parties.
- Spec hardened (was conditional, now verified): **Eldritch Blast must be excluded by name** — its record is present and beam-scaled ("separate attack roll for each beam"); generic N×tier would report "3d10" for what is 3 separate 1d10 attack rolls. Fix otherwise as audited: tier = 1+(lvl≥5)+(lvl≥11)+(lvl≥17), `base_damage = f"{N*tier}d{M}"`, keep `base_damage_level1` (additive).
- test: Acid Splash at 1/5/11/17 → 1d6/2d6/3d6/4d6; Eldritch Blast falls back to prose; Fire Bolt curated path unchanged; leveled spells untouched.
- dup: new.

### F3-3 [P1|high|S] **CONFIRMED — census figure corrected.** No ritual casting; the #1-cast spell in QA (Detect Magic, ritual) burns a L1 slot every time
- Verified: `srd.ritual` loaded but never read (grep: one unrelated narrative docstring); `if spell_level > 0:` spends unconditionally (5320-5327); no `as_ritual` param. Detect Magic record `{level:1, ritual:True, concentration:True, duration:'10 minute'}` confirmed.
- **Measurement corrected:** the "24 casts" double-counts — `"spell_name"` (call param) ≈12 + `"spell"` (alias AND the result echo field) ≈12. Distinct engine casts ≈ 12. Detect Magic remains the clear #1 cast spell in QA (next: Mage Armor). Severity P1 stands on the unchanged structural fact: the most-used utility cast pattern over-charges the scarcest caster resource on every single cast.
- Fix as audited (invariant-clean): additive `as_ritual: bool = False` — require the ritual flag, skip the slot spend, keep concentration/duration; refuse in active combat; surface `ritual_available: true` on normal casts of ritual spells. Default False = byte-identical.
- test: ritual cast leaves slots unchanged + sets concentration; as_ritual on non-ritual raises; in-combat as_ritual raises.
- dup: new (no ritual issue in open list; #350's "Rest&Prepare" is viewer-side).

### F3-4 [**P2** (was P1)|high|M] **CORRECTED — severity downgraded + spec fixed.** AoE/multi-target casts have no engine path
- Verified: single `target_id` signature (5219-5230); saving_throw single-target; no batch tool; shape_type/size (52 spells) never surfaced. The ≈2+2N round-trip arithmetic is right.
- **Downgrade rationale:** no field-observed mechanical drift was cited (unlike F3-3's census); the latency-budget gate is itself still being DEFINED (in-flight #753); cast_spell's documented degrade contract explicitly hands AoE resolution to the DM. Real, high-value, but not gate-breaking today → P2.
- **SPEC ERROR corrected:** "per-row errors for missing ids after the slot is spent" violates the engine's verified rejection-before-state-change discipline (all five cast_spell rejection paths fire before `slot.used += 1` — clean-verified #1). Corrected: validate ALL target_ids upfront and reject the whole cast cleanly BEFORE the slot spend. Rest as audited: `target_ids: list[str] = []` additive; engine rolls damage ONCE; per-target saves via existing save_modifiers; full/half through combat.apply_damage; per-target result table; one lock, one write; surface `shape` in srd-path results; empty list = today's behavior.
- test: Burning Hands slot-2 vs 3 targets — one shared damage roll, per-outcome halving, slot spent once, paralyzed target auto-fails DEX; unknown id in list → rejected with slot unspent.
- dup: new.

### F3-5 [P1|high|S] **CONFIRMED.** Out-of-combat expiry of a save-ends marker strands its condition — Hold Person victim paralyzed forever
- Fully re-verified, every link in the chain: add_condition's marker sets no scale → models default `scale="rounds"` (models.py ActiveEffect; server.py:2729-2739) and `concentration=False`; any phase advance routes ALL characters through `_expire_clock_effects_all` (5200-5215) → `expire_clock_effects` (combat.py:584-611) drops rounds/minutes effects with NO repeat-save exemption (contrast tick_round_effects' explicit exemption, 568-574); `_commit_expiry` (549-557) clears concentration only, never `imposes_condition`; `end_repeat_save_effect` (644-654) is the sole condition-lifter and is reachable only from next_turn's two in-combat paths + drop_concentration — and once the marker is dropped, the inverse sweep (3620-3630) and drop_concentration's loop (4424-4432) have nothing to match. remove_condition is the only manual out. short_rest path (expire_short_rest_effects 614-630) identical.
- Fix-spec checked SAFE and minimal: only add_condition sets imposes_condition and always alongside repeat_save, and tick_round_effects exempts repeat-save markers — so teaching `_commit_expiry` to lift `imposes_condition` cannot misfire in combat. Correct 5e outcome (the minute elapsed; victim freed). Caster's own minute-scale twin expires in the same all-characters sweep, clearing concentration symmetrically. Pure helper change, no schema.
- test: red-first — out-of-combat hold + rider, advance_time one phase: marker gone AND paralyzed cleared AND caster concentration None; short_rest variant; plain-Bless expiry regression.
- dup: new.

### F3-6 [P2|high|M] **CONFIRMED — spec broadened.** 4 of 5 concentration-end paths leave the held victim locked until the next next_turn
- Verified: the freed-targets loop exists ONLY in drop_concentration (4424-4432). Failed concentration_save (4380-4384), caster incapacitation (add_condition 2702-2704), caster 0-HP (combat.py:340-343 — Character-pure, can't see the victim), and cast_spell's concentration replacement (5329-5336) all clear caster-side only. In-combat backstop = next round's sweep; out of combat, none (compounds with F3-5).
- Spec correction (minor but load-bearing): the 0-HP site must hook EVERY server-side damage path that can down a caster — the apply_damage tool AND attack()'s auto-apply — not just the apply_damage wrapper; and the extracted `_release_held_targets(c, caster_id, spell_name)` must capture `was` before clearing (auditor had this) and keep the repeat_save+source_id match (plus F3-1's linked-child extension if both land). Sweep stays as backstop.
- test: fail a concentration save in combat → victim's paralyzed clears in the same result; repeat for recast, caster-downed-via-attack, caster-downed-via-apply_damage, incapacitation; follow-up attack() no longer auto-crits.
- dup: new (#596 = viewer reaction-pip/conc-warning UI; #618 = drop_concentration player-verb relay — both adjacent, neither owns this).

### F3-7 [P2|high|S] **CONFIRMED.** known/prepared cast gate is case-sensitive vs canonical names; learn_spells/prepare_spells store unvalidated raw strings
- Verified: gate at 5316-5318 compares canonical (`spells.py` lookups casefold on READ — :21,:25,:40,:52 — then return the canonical-cased record name) against raw stored strings; learn_spells/prepare_spells (6235-6253) store `list(spells_list)` with no canonicalization/validation and replace wholesale. `learn_spells(["fireball"])` → "Fireball" ∉ {"fireball"} → every cast rejected, any input casing (the caller-side name is canonicalized before the compare, so no casing the DM types can match). Field sanity re-confirmed: the 8 observed QA rejections were legitimate (Rolan genuinely lacked Thunderwave/Misty Step) — gate is load-bearing, the hole is code-proven.
- Fix as audited, invariant-clean: canonicalize+validate on write (reject unknown, listing them); casefolded compare on read for legacy snapshots; additive `mode:"add"|"replace"="replace"`.
- test: learn lowercase → cast canonical passes (currently raises); typo errors at learn time; mode="add" appends.
- dup: new; adjacent to in-flight #754 (viewer spellbook pool + chargen population) and #617 (prepare_spells move-kind relay) — neither owns engine-side canonicalization.

### F3-8 [P2|med|S] **CONFIRMED** — prepared-caster discipline is a no-op (cast gate unions known with prepared)
- Verified: 5316 `known = set(ch.spells_known) | set(ch.spells_prepared)`; Rolan's snapshot confirms known (7) ⊋ prepared (4) so preparation has zero mechanical weight. Skeptic note kept at med confidence: the docstring ("If spells_known/prepared are set, the spell must be among them") reads as a deliberate lenient design, so this is a design-tightening with a RAW justification, not an unambiguous defect. Fix's legacy guard (empty prepared list keeps the lenient union) makes it additive-safe; prepared-count cap surfaced via warnings only.
- test: wizard with prepared subset — unprepared leveled cast rejected; empty-prepared legacy allowed; cantrips exempt; sorcerer unaffected.
- dup: enriches #754 (engine-side counterpart). depends_on: F3-7, F3-3 (ritual carve-out).

### F3-9 [P2|high/med|M] **CONFIRMED — strengthened.** Spell action economy is record-only
- Verified: cast_spell sets `c.combat.action_used = True` (5418-5419) but nothing checks it for casts; attack's gate (combat.check_action_attack:129-170) reads only attack counts → double-cast, cast+Attack, and Attack+cast are all legal; casting_time loaded (curated records carry "1 action"/"1 bonus action" — re-dumped) but never read for economy → all 23 bonus-action spells consume the ACTION flag; 40 minute / 15 hour casts resolve as one combat action.
- **Strengthening found:** use_action DOES check action_used (3716-3728) — so after a Healing Word "cast" the engine wrongly REFUSES use_action(kind='action') even though RAW the action is still available. The asymmetry is live in both directions, not just permissive.
- Fix as audited (resolve casting_time in the combat-active branch; bonus-action → bonus_action_used; action → reject when action_used, honoring surge_actions; minute/hour → refuse in combat; symmetric attack-side check guarded to action_attacks_made==0). Surge-interaction policy stays med-confidence design review. Out of combat byte-identical.
- test: cast→cast rejected; cast→attack rejected; Healing Word→Fireball allowed; Healing Word→use_action(kind='action') allowed (currently refused); Action Surge enables second cast; out-of-combat unaffected.
- dup: new (#166 is DM-adherence/class-feature narration cluster by title; flag for synthesis-time overlap check, not a dup on evidence available).

### F3-10 [**P3** (was P2)|high-defect/low-impact|S] **CORRECTED — severity downgraded.** Multiclass save DC/attack/heal mod use the FIRST caster class's ability for every spell
- Verified: `_casting_mod` (382-396) returns the first caster class's modifier; spell_save_dc (5166) and cast_spell (5405) ride it; `_CASTING_ABILITY` map (srd_tables.py:185-195); both data sources carry `classes` (curated `["Cleric","Paladin"]`; srd524 `["srd-2024_wizard"]` — fix must strip the `srd-2024_` prefix). concentration_save has no advantage param (4372).
- **Downgrade rationale (measured):** ZERO multiclass characters exist in any QA play-state snapshot — the defect is real but its field impact is currently nil, and single-class characters are byte-identical through the proposed fix. P3 until multiclass enters QA personas.
- Fix as audited: best modifier among classes∩spell.classes, fallback to today; optional `casting_class` override; `advantage` param on concentration_save. Additive.
- test: Cleric1/Wizard9 casting a wizard-only spell uses INT regardless of class order.
- dup: new.

### F3-11 [P2|med|M] **CONFIRMED — impact wording corrected.** NPC/monster casters can't route through cast_spell (no spawn path seeds spell_slots)
- Verified: leveled casts require `ch.spell_slots[lvl]` (5324-5326); bestiary.py has zero spell hits; stat-block spawns (2015-2030, 2090-2100) seed ac/hp/resistances only; Creature.json: 330 creatures, 0 spellcasting traits (re-run); the no-slot error gives no workaround hint. Cantrips DO work for monsters today (no slot needed; _casting_mod's classless mental-stat fallback at 392-396 is correct).
- **Overstatement corrected:** "feeding the F3-5/F3-6 stranded class" — an enemy Hold Person applied via roll()+add_condition(repeat_save_*) still self-enforces end-of-turn saves WITHOUT a concentration link (add_condition deliberately drops source_id for non-tracked concentration, 2712-2733), so the victim is NOT stranded in combat; what's lost is the early-release path (breaking the monster's concentration can't free the PC) and slot/concentration state integrity. Out-of-combat F3-5 exposure is the same as for PCs. P2 kept on the corrected, narrower basis: enemy casters are routine in combat QA and currently bypass engine spell state entirely.
- Fix as audited: additive `innate: bool = False` skips slot check/spend (slot_used:"innate"), keeps concentration/duration/rider/DC; enrich the no-slot error for monster/npc casters. Monster spell-list data = bestiary unit's half.
- test: spawned monster casts Hold Person innate=True — no slot error, concentration tracked, rider surfaced; drop_concentration frees the PC (composes with F3-6).
- dup: new.

### F3-12 [P3|high|S/M] **CONFIRMED.** Spell components / material costs entirely absent (data and engine)
- Verified: srd524 carries verbal/somatic/material/material_specified/material_consumed/material_cost — material_cost None on all 339 (re-run); curated `components` strings ("V, S, M") present and unread; cast_spell reads none. Deliberate-absence parity; surfacing-only half recommended (S), costly-component enforcement needs data backfill behind a house_rule (M).
- dup: new.

### F3-13 [P3|med|M] **CONFIRMED.** Reaction spell Shield can't do its job — attack() resolves atomically; +5 AC can never flip the triggering hit
- Verified: attack() rolls, arbitrates parry (3941-3974 — the proven auto-arbitration pattern, keyed on numeric `target.parry`, no spell awareness), applies damage, persists in one call; no declare/undo seam; Shield's 1-round ActiveEffect is inert (F3-1). Fix as audited (parry-pattern auto-arbitration: Shield-knowing defender + unspent reaction + available slot + `hit and not crit and total < ac+5` → spend, flip to miss, surface `shield_cast`; behind house_rule/per-character opt-in). depends_on F3-1.
- dup: new (#596's reaction pip is viewer UI).

### F3-14 [P3|high(a,b)/med(c)|S] **CONFIRMED.** Polish bundle: slot-error affordance; warlock pact recompute refills used slots; multiclass warlock loses pact slots
- Verified: (a) 5325-5326 error names only the requested level; (b) `_recompute_spellcasting` line 345 writes pact `used=0` unconditionally while the multiclass branch right above preserves used (338-340) — level-up silently refills a spent pact pool (small harm window: pact refills on short rest anyway, but the asymmetry is a plain bug); (c) line 342 `len(class_levels) == 1` gate drops pact slots entirely on multiclassing — documented deferral (rests.py short_rest docstring: "multiclass Warlock pact recovery is not modeled") but it is a slot REGRESSION on level-up, not just a missing feature. Fixes as audited; (c) stays a documented med-confidence design choice (one-dict approximation).
- test: warlock with used pact slot levels up → used preserved (red-first); error lists available slot levels.
- dup: new.

---

## CLEAN-VERIFIED (skeptic spot-checks)
Items 1-16 of the auditor's clean list were spot-checked where load-bearing; all held:
- #1 rejection-before-state-change: re-read 5283-5336 — incapacitated (5289) → turn/reaction (5303-5315) → known (5316) → downcast (5322) → no-slot (5325) ALL precede `slot.used += 1` (5327) and concentration set (5336). Confirmed (and used to correct F3-4's spec).
- #3 single-concentration replacement (5329-5336), #5 drop_concentration release loop (4424-4432), #6 reaction gating before any spend (5302-5315), #10 on-hit rider defer + refresh-not-stack (5357-5404), #11 Mage Armor formula capture/apply (5393-5395, 3773-3804), #4 damage→conc pipeline + 0-HP clear (combat.py:296-347), #12 rests (rests.py short_rest pact + _commit_expiry conc twin), #13 resources (use_action economy block 3712-3743 cross-checked for F3-9), #16 the 8 QA rejections were legitimate — all confirmed by direct read.
- #2/#8/#9 "live-verified" math claims: logic re-derived from spells.py (resolve_effect scaling/upcast/negative-mod path, parse_duration unit table, repeat_save_rider regex + single-condition conservatism) — consistent; not re-executed.
- Test-gap census: 21 test functions across test_spellcasting/test_spell_automation (auditor said 20) — THIN flag stands; zero coverage for concentration_save tool, drop_concentration release, ritual, casting_time economy, learn/prepare canonicalization, multi-target, clock-expiry of repeat-save markers, warlock pact recompute, multiclass DC.

## Severity ledger
- Kept P1: F3-1 (engine advertises buffs then rolls without them — and they are unrepresentable, not just unautomated), F3-2 (actively wrong structured numbers; level-5/10 casters exist in QA today), F3-3 (slot over-charge on the #1 cast pattern), F3-5 (permanent paralysis with no engine out — zero_critical class).
- Downgraded: F3-4 P1→P2 (no field-observed drift; latency gate undefined; documented degrade contract), F3-10 P2→P3 (zero multiclass characters in any QA state).
- No P0s claimed or warranted (nothing breaks a gate TODAY by itself).

## Invariant compliance of fix-specs (post-correction)
All 14 specs are additive (new optional params/fields with defaults = byte-identical), keep the engine sole-writer (mutation under campaign_lock), keep engine-rolls-and-tells, and round-trip old snapshots. Corrections applied where the original spec violated an invariant or relied on a non-matching mechanism: F3-1 (sweep extension required), F3-4 (validate-before-spend), F3-6 (attack() damage path included).

════════════════════════════════════════════════════════════════
## UNIT 04 — VERIFIED REPORT (verbatim from unit-04-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 4 — WORLD / TRAVEL / REST / CLOCK — SKEPTIC-VERIFIED REPORT
Verified at /Users/lume/ClawDnD-val @ a245a2c (HEAD; f24a102 ancestor), 2026-06-11.
Method: every cited file:line re-opened on main; measurement claims re-run (wander math via `uv run python`, 152-snapshot region census, F4-9 cap-stall repro); dup_status re-checked against /tmp/engine-audit/open-issues.txt; fix-specs checked against engine invariants (sole-writer, additive round-trip, engine-rolls, frozen wire contracts).

**Tally: 10 confirmed, 4 corrected, 1 refuted.**

---

## F04-1 [P1|high|M] Region danger/creature tables never match ANY shipped content — wilderness ambush model fires inside Baldur's Gate city streets — CONFIRMED
- **Verification:** All 14 `content/worlds/baldurs-gate/areas/*.json` carry `"region": "Baldur's Gate"` (re-measured: `grep | uniq -c` → 14). No REGION_RATES/_REGION_TIER keyword is a substring of "baldur's gate" (table inspected, wander.py:41–107). Re-ran the math: `encounter_chance("Baldur's Gate") == 0.30`, pool == wilderness (Wolf/Boar/Giant Spider/Worg/Brown Bear/Dire Wolf/Ogre); composite `"Baldur's Gate — The Lower City city hub"` → 0.08 civilized. All three staging seams pass `loc.region` only: travel_to server.py:1014–1021, long_rest server.py:6055–6066, roll_wandering_encounter tool (~server.py:8480s, `cur_loc.region`); `_stage_wandering_encounter` (server.py:2116–2210) does not enrich the string. Authored regions seeded WITHOUT `region` (content.py:1609–1625 omits the kwarg; `Location.region` default `''` verified); ingested areas DO set region (content.py:1664) = "Baldur's Gate" for all 14. Tags ("city" on every BG node, verified in 4 sample files) are joined into `notes` (content.py:1665) — a surface the matcher never sees. `house_rules.wandering_encounters: bool = True` (models.py:1176). Re-ran the snapshot census: 152 play-state snapshots, region values = "Baldur's Gate" (2127 locs), '' (1166), 'Lower City' (3 — immaterial rescue, auditor's "only two values" slightly imprecise).
- **Typed-encounter nuance (kept, severity unchanged):** only ~40% of staged hits draw combat (DEFAULT_TYPE_WEIGHTS, wilderness tier unbiased) — but the non-combat draws use wilderness-flavor descriptors ("a washed-out ford") inside city streets, equally wrong-genre, and the 0.30 frequency vs the intended 0.08 stands regardless of type.
- **Fix (corrected one nit):** (1) at the three staging seams build the MATCH string as `f"{loc.region} {loc.name} {loc.notes}"` — but pass the composite ONLY to `roll_encounter`/`pick_typed_encounter`; keep the returned payload's `region` key as `loc.region` (don't silently change the wire value's semantics). Add urban keywords with ordering discipline: `"sewer"`/`"undercity"` BEFORE `"city"` (substring hazard — "undercity" contains "city"), plus market/tavern/harbor/dock/port/slum/warren/quarter/temple/palace in the civilized band. (2) Content: set `region` when seeding authored regions (content.py:1609) — e.g. the region's own name. Additive; wander.py stays pure.
- **Test:** red-first: `encounter_chance(composite for Lower City) ≤ 0.12` + civilized pool; sewers → non-civilized tier; integration: seeded BG campaign, seeded-rng wander roll at loc-lower-city never stages a wilderness-pool creature; ≥90% of 27 BG locations resolve a non-BASE_RATE keyword.
- **dup:** new — #601 (danger on map pins) and #381 (route_kind metadata) are presentation-side; both verified open, neither touches the resolver. Not owned by #748–#758.

## F04-2 [P1|high|M] Production soft-tick consumes one-shot world beats / backlog developments / effect expiries SILENTLY — CONFIRMED
- **Verification:** `clawdnd_soft_tick` (qa/lib_beat_driver.sh:631–672) calls `server.advance_time(camp, phases=1)` and prints ONLY `day`/`time_of_day` — `world_beats`/`world_developments`/`expired_effects` in the return are discarded to run-log stderr. **scripts/play.sh:504 (production loop) calls the same function each beat** — verified. Thread beats re-arm +4 days on fire (worldsim.py:57–63) → moment consumed; deterministic backlog items flip `resolved` (worldsim.py:183–184) un-narrated; `needs_llm` items flip `status="fired"` (worldsim.py:181) — grep across servers/engine: **zero readers of status "fired"** (`pending_backlog` filters `status == "pending"`, worldsim.py:205; `world_tick` returns only fired-this-call, server.py:7330–7358; get_state and scene_context expose no backlog surface — verified by grep; the "later DM digest" does not exist). Next-beat runbook reads numeric progress only (no world_beat/backlog refs in lib_beat_driver.sh/play.sh — verified by grep).
- **Fix (spec verified against invariants):** (1) harness-side: soft_tick appends the returned beat/development/expiry lines into the NEXT beat's runbook block ("While time passed: …") — zero engine change, closes the production leak. (2) Engine: additive `BacklogItem.woven: bool = False` (defaulted — old snapshots round-trip) + an optional `scene_context` field listing fired-but-unwoven items + a `mark_woven` write tool (record_camp_beat pattern, sole-writer preserved).
- **Test:** engine — needs_llm item due tomorrow, advance_time(4): new surface lists it (today nothing does); harness — soft_tick over a state dir with a due beat: next runbook text contains the line.
- **dup:** new — #749 (verified merged at HEAD) fixed heartbeat *contamination* (wrapper-progress lines leaking into recap/FTS); this is content *loss* at the same layer. Distinct.

## F04-3 [P2|high|S] No one-long-rest-per-24h guard — repeatable free instant full-party restore — CONFIRMED
- **Verification:** server.py:5996–6075 — no per-character last-rest record anywhere; each call runs `rests.long_rest(ch)` (full HP/slots/pools/hit-dice/−1 exhaustion, rests.py:79–108); at morning `steps == 0` → zero time cost → 6 calls clear exhaustion 6→0 instantly. tests/test_rests.py: 15 tests (re-counted), zero repeated-rest coverage; `test_long_rest_at_morning_is_a_clock_no_op` (line 140) asserts the enabling behavior as clock-only. short_rest costing no clock time is documented sub-phase resolution (leave as-is; hit dice limit it).
- **Fix (spec verified, one ordering note added):** additive `Character.last_long_rest_day: int = -1` (defaulted — round-trips). Guard at seam ENTRY (BEFORE `rests.long_rest(ch)` mutates): if `ch.last_long_rest_day == c.day` → `{ok: False, error: …}` no-state-change (use_resource pattern, server.py:6116). Stamp AFTER the clock advance (the morning the rest finished). Per-character stamps preserve party convergence.
- **Test:** same character long_rest twice same day → second `ok: False`, state unchanged; party-of-3 convergence regression green; rest day N then N+1 allowed.
- **dup:** enriches #592 (camp & rest pillar — "not theater"; verified open). #610/#611 are viewer/supplies, don't own this.

## F04-4 [P2|high|S] Long rest neither clears temp HP nor (degraded-path) concentration — CONFIRMED, scope CORRECTED
- **Confirmed:** (1) temp HP — `ch.temp_hp` (models.py:633) never touched by rests.py (grep: zero hits) or the server seam; SRD 5.2: temp HP last "until depleted or you finish a Long Rest". (2) Degraded-path concentration — cast_spell sets `ch.concentration` for every concentration spell (server.py:~5357) but registers the effect twin only when `duration is not None` (server.py:~5365); a twin-less concentration is cleared ONLY via `_commit_expiry` of an effect (combat.py:549–557) — never on rest.
- **CORRECTED (auditor sub-claim refuted):** "rest taken AT morning (0 steps) leaves a not-yet-due effect" is wrong for the common case — the server seam runs `_expire_clock_effects_all(c, long_rest=True)` UNCONDITIONALLY (server.py:6048, explicitly "even when resting in the morning is a clock no-op"), which kills minute/round-scale AND every hour-scale buff; `_commit_expiry` then clears twinned concentration. The residual concentration leak is ONLY the degraded (no-twin) path plus exotic day-scale concentration effects. temp_hp half unaffected and is the load-bearing defect.
- **Fix:** rests.long_rest: `ch.temp_hp = 0` (+ additive result key). Server seam after the rest: if `ch.concentration`: set None + `combat.expire_concentration_effects(ch)` (combat.py:636–644), append names to `expired_effects`. Rester-only (others' buffs handled by the clock sweep — correct).
- **Test:** temp_hp=8 → long_rest → 0; degraded-path (concentration set, no effect twin) → cleared + named.
- **dup:** new (enriches #592). #618 (drop_concentration relay) is an unbuilt player verb, doesn't own the rest seam.

## F04-5 [P2|high|S] downtime(days=0) (or negative) REWINDS the clock — night becomes morning of the same day — CONFIRMED, fix-spec CORRECTED
- **Verification:** server.py:7944–7946: `elapsed = max(0, int(days)); c.day += elapsed; c.time_of_day = "morning"` — the morning reset is unconditional. Day 3 night + downtime(0) → day 3 morning (backward). Behavioral gate `world_advanced_time` (qa/assert_behavioral.py:467–471, re-read: `day > 1 or tod not in ("", "morning")`) is flippable false on day 1. Only non-monotonic clock path (siblings floor at 0 steps and are true no-ops).
- **CORRECTED fix-spec:** the auditor's "the tick calls are elapsed-idempotent no-ops anyway" is wrong for `worldsim.tick` — thread beats fire whenever `trigger_day <= day` regardless of elapsed (worldsim.py:58), so downtime(0) today can also fire+re-arm a due thread. Corrected spec: for `elapsed <= 0` return EARLY with the current clock + a note pointing at advance_time — skip the mutation AND all three ticks/the expiry sweep.
- **Test:** day=2 night → downtime(0) → still day 2 night (currently morning); downtime(-3) same; downtime(2) → day 4 morning regression.
- **dup:** new (verified — no open issue owns the downtime clock).

## F04-6 [P2|high|S] add_location(make_current=True, advance_time=True) advances the clock without sibling side-effects — CONFIRMED, framing CORRECTED
- **Verification:** server.py:935–939 — only `advance_clock` + `worldsim.tick(max_beats=1)` + `tick_backlog(max_events=1)`. travel_to's advance path (server.py:994–1023) additionally runs `tick_strategic` (1003), `_expire_clock_effects_all` (1006), `_stage_wandering_encounter` (1016). The downtime comment ("this was the only seam that omitted it", server.py:7951–7953) shows the expiry-sweep audit missed this seam.
- **CORRECTED framing:** "effect expiry does not [self-heal]" is overstated — expiry is deadline-conditional, so the NEXT sweep-bearing seam (incl. the every-idle-beat soft-tick advance_time) expires the overdue buff late. The real harm window: a fight staged immediately on live-gen arrival uses stale buffs (Bless/Mage Armor past deadline) — exactly this seam's use case. Severity P2 retained on that basis; permanent leak claim dropped.
- **Fix:** mirror travel_to inside the `advance_time and arrived` block: `expired_effects`, `strategic_events`, and (optionally, gated `not c.combat.active`) the destination wander roll using F04-1's composite. Additive payload keys; the `arrived` gate keeps self-target calls a clock no-op.
- **Test:** minutes-scale effect + add_location(make_current, advance_time) → effect gone + named (currently survives); advance_time=False path byte-identical.
- **dup:** new. depends_on: F04-1 (wander-roll part only).

## F04-7 [P2|high|S] long_rest rolls the day over but never ticks the world — CONFIRMED, impact CORRECTED (delay+production-loss, not unconditional loss)
- **Verification:** server.py:5996–6075 calls none of worldsim.tick / tick_backlog / tick_strategic (grep of tick_backlog call sites: 939, 1000, 7342, 7949, 8020 — long_rest absent), while every other day-moving seam has the trio.
- **CORRECTED impact:** standalone, the dawn beat is DELAYED, not lost — it fires at the next tick-bearing seam and is surfaced in THAT payload. In production the next seam is usually the soft-tick, which discards it (F04-2) — so loss-in-practice is the F04-2 interplay, and the standalone defect is the beat landing at the wrong narrative moment (mid-next-leg instead of "the world moved while you slept"). P2 retained: canonical seam inconsistency + the production interplay.
- **Fix:** inside the existing once-per-overnight `steps > 0` gate (server.py:6054): `out["world_beats"]=…tick(c, max_beats=2)`, `out["world_developments"]=…tick_backlog(c, max_events=2)`, `out["strategic_events"]=…tick_strategic(c)`. Convergence free via the gate; additive keys; day-elapsed idempotence prevents double-advance.
- **Test:** thread + backlog item due tomorrow; long_rest from evening → both keys non-empty + thread re-armed; second member's morning rest → empty.
- **dup:** new — #609 (honest camp clock; verified open) is viewer-side read-only. Sibling, not owner.

## F04-8 [P2|high|M] Wandering-combat picker is not level-banded — CONFIRMED
- **Verification:** wander.py:293 `r.choice(pool)` — uniform, level-blind kind draw; `_count_for_budget` (238–259) floors at 1 and fields the cap (12) on a cap-miss. Re-ran the math: Wraith (1800 XP) vs party [1,1] → band "deadly", count=1 → staged (1-in-6 undead-pool draw); Bandit (25 XP) ×12 vs [15,15,15,15] → band "trivial", count=12 → staged. Violates the module's own contract ("sized to the party's XP budget", target_difficulty="medium"). `must_offer_out` (outlook folded at server.py:2200–2210) mitigates only the over-match direction; trivial staged fights still waste 1–3 ~100s beats.
- **Fix:** pre-filter `_resolved_pool` by unit XP vs party budget BEFORE the seeded draw: drop candidates ≥2 bands over target as a single unit AND candidates that can't reach the target band even at 12; nearest-band fallback when the filter empties (never [] for a non-empty pool). Pure-module; return shape unchanged; outlook stays as residual net.
- **Test:** seeded 200-draw sweep: party [1,1] "haunted" never stages ratio ≥4 nor band ≥2 over target; party [15]*4 "city" never stages an all-12 trivial; existing wander/typed suites green.
- **dup:** new (verified — no open issue on wandering level-banding).

## F04-9 [P3|high|S] tick_backlog cap stall — same-trigger-day cap-truncated items can't fire until the NEXT day — CONFIRMED (reproduced)
- **Verification:** REPRODUCED on main: 3 items trigger_day=5, cap 2, day 5 → first call fires 2, second same-day call fires **0** (`last_tick_day` = 5 → `elapsed = 0` no-op, worldsim.py:163–164,195), day 6 fires the third. Contradicts the inline "same/next day still drains" comment (worldsim.py:170–172). Cross-day backlogs drain correctly.
- **Fix:** when capped set `bl.last_tick_day = max(bl.last_tick_day, last_fired_trigger_day - 1)`. Verified against re-fire: one-shots are status-guarded (resolved/fired ≠ pending); recurring items re-arm to `day + cadence > day`. One line.
- **Test:** 3 items same trigger_day, cap 2 → tick twice same day → all 3 fired (currently 2).
- **dup:** new.

## F04-10 [P3|high|S] move_to_zone OA advisory wrong twice — unconscious hostiles provoke; KIND-based sides flag allied NPCs — CONFIRMED
- **Verification:** server.py:~3256–3270 (re-read): skips only `other is None or other.dead`; no `current_hp <= 0`/incapacitated skip; hostility = `kind in {player, companion}` vs everything else — `Character.attitude` (exists, default "") never consulted, so a friendly-attitude NPC in the from-zone is flagged as a provoker. Advisory only (OA never auto-rolled — clean-verified #12) → P3 correct.
- **Fix:** also skip `current_hp <= 0` / incapacitating conditions; treat friendly-attitude or in-party NPCs as party-side. Advisory contract unchanged.
- **Test:** downed monster + friendly NPC in from-zone → provokers excludes both (currently includes both).
- **dup:** new — #596 (verified open) renders the OA signal viewer-side; doesn't fix the computation.

## F04-11 [P3|high|S] Camp membership rule contradicts the travel/XP rule — de-facto companions never appear at camp — CONFIRMED
- **Verification:** camp_scene (server.py:~6893–6905) and `_living_companions` (companion_banter.py:25–34) iterate `c.party` ∩ kind=="companion"; long_rest camp_hint (server.py:6072) gates `for i in c.party`. `_move_party_to` (server.py:230–235) and `_party_xp_recipients` (239–275) deliberately include kind=="companion" NOT in c.party (the #353/#739 de-facto rule, explicit in their docstrings) — camp is the one seam the rule never reached.
- **Fix:** extract one membership helper (kind=="companion" ∪ c.party, living) and use it at all three camp sites.
- **Test:** companion loaded add_to_party=False → camp_scene.present includes them; schedule_camp_beats yields their solo beat.
- **dup:** new.

## F04-12 [P3|high|S(a)/M(b)] _move_party_to drags DEAD companions; no first-class departure state — CONFIRMED
- **Verification:** server.py:231–235 — `travels = cid in party_ids or member.kind in ("player","companion")`, no `member.dead` filter (contrast `_party_xp_recipients`, which excludes dead at 263). A dead companion's corpse teleports to every new scene.
- **Fix:** (a) skip dead in the sweep; (b) later `part_ways` tool (remove from c.party + departed flag) under the camp/rest pillar.
- **Test:** dead companion at A, travel to B → companion stays at A (currently moves).
- **dup:** new; (b) enriches #592/#58 (both verified open). Confidence: high(a)/med(b).

## F04-13 [P3|high|S] long_rest watch credit requires exact single-token match — CONFIRMED
- **Verification:** server.py:6056–6059 — `watch_lower in ("careful", "camouflage", "camouflaged", "hidden", "concealed", "stealth", "stealthy")` is whole-string membership; "we keep a careful watch" earns no −0.15 camouflage modifier.
- **Fix:** `any(k in watch_lower for k in keywords)` substring credit (factor a modifier helper for testability).
- **Test:** "we set a careful watch" → camouflage applied.
- **dup:** new.

## F04-14 [P3|high|S] bloomridge-market ships an unresolvable connection hint "the Cloistered Quarter" — CONFIRMED
- **Verification:** `grep -rn "Cloistered" content/` → exactly ONE hit: content/worlds/baldurs-gate/areas/bloomridge-market.json:9 (the hint itself); no such area/region exists. Seed-time name-resolution (content.py:1695–1701) leaves unmatched names verbatim; travel rejects them; no warning anywhere.
- **Fix:** author/ingest the cloistered-quarter area (wiki-first, per owner direction) or drop the hint; add a seed-time warning for unresolved connection hints.
- **Test:** content test: post-seed, every connection id of every BG location resolves (currently 1 failure).
- **dup:** new.

## F04-15 — REFUTED: seed_threads thread-id collisions have no live path on main
- **Auditor claim:** `worldsim.seed_threads` re-mints from `thread-1` each call (true — worldsim.py:38); two call sites "normally exclusive but unenforced" → colliding thread_ids.
- **Refutation (the guard the auditor missed):** `worldsim.seed_threads` is the ONLY creator of thread-tagged consequences (grep: no other `thread_id=` writer). Call site 1 (content.py:1802, seed_world) operates on a FRESH `Campaign` constructed at content.py:1596 — no pre-existing threads to collide with. Call site 2 (content.py:917, the ending-overlay path in seed_campaign) **clears ALL thread-tagged consequences first** (content.py:895: `c.consequences = [cq for cq in c.consequences if not cq.thread_id]`) and reseeds ONCE from the merged set — with an inline comment naming exactly this hazard: "so no retired thread keeps ticking **and ids don't collide with the overlay's (B-LOW-1)**". The collision was already found and guarded. No caller can produce duplicate thread_ids today; `tick` re-arms in place so even a hypothetical dup would tick, not break.
- **Residual:** a future caller that appends without clearing would collide — at most a hardening docstring/assert nicety, below the filing bar. Not carried to the backlog.

---

## CLEAN-VERIFIED (auditor's list, skeptic spot-checks in [brackets])
1. travel.travel_to graph honesty — [spot-read travel.py; edge-gating + reachable-exits error present].
2. advance_clock math — [read; rollover + steps≤0 no-op correct].
3. BG post-seed graph integrity (27/27 reachable; F04-14's hint the sole exception) — [reverse-edge wiring read at content.py:1695–1707; consistent].
4. long_rest per-member morning convergence + once-per-overnight camp-watch gate — [read server.py:6028–6068; tests at test_rests.py:120–156 confirm].
5. rests.short_rest mechanics — [read rests.py:36–76; clamps + single-class warlock pact recovery as documented].
6. 0-HP/dead rest gating — [read rests.py:42–43, 84–87; RAW-correct].
7. long_rest restoration scope — [read rests.py:88–100; min-1 hit-dice house rule documented].
8. advance_time combat guard + soft-tick combat skip — [read server.py:7989–7998 + lib_beat_driver.sh:643–648; both sides present].
9. consequences.due excludes thread beats — [consistent with worldsim docstrings; not deep-checked].
10. tick_backlog elapsed-day idempotence + tick_strategic cursor math — [read worldsim.py:161–165, 213–239; cadence math correct; idempotence reproduced in F04-9 repro].
11. _stage_wandering_encounter contract — [read in full, server.py:2116–2210; flag gating, all-spawn-fail → None, never auto-starts].
12. Zones tools incl. OA-never-auto-rolled — [read server.py:3240–3282; modulo F04-10].
13. set_pacing advisory-by-design — [accepted; QA-side note, not an engine defect].
14. world_tick discloses every mutation in its own return — [read server.py:7330–7358; beats + developments + pending + strategic all surfaced — the violation is the soft-tick CALLER (F04-2)].
15. campaign_calendar display-only — [spot-read; canonical_day echoed].
16. look_around walk_minutes presentation-only — [spot-read travel.py:134–177].
17. _move_party_to/_party_xp_recipients membership coherence — [read in full, server.py:210–275; residuals are exactly F04-11/F04-12].
18. add_location non-clock behavior — [read server.py:920–961; orphan warning present].
19. Latency posture — [accepted; no token-mass findings].

## VERDICT (verified)
The auditor's report survives skeptical review almost intact: the pure modules are solid, and the unit's real holes are seam-level — the dead region-danger model vs all shipped content (F04-1), the production soft-tick content leak (F04-2), and the exploitable/incomplete rest seam (F04-3/F04-4). One finding (F4-15) was a plausible-but-wrong collision claim already guarded by the B-LOW-1 clear-before-reseed; three others needed scope/spec corrections (the morning-rest concentration sub-claim, the downtime(0) "ticks are no-ops" spec line, the add_location/long_rest "lost forever" framing — both are late/wrong-moment, with true loss only via the F04-2 interplay).

════════════════════════════════════════════════════════════════
## UNIT 05 — VERIFIED REPORT (verbatim from unit-05-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 5 — VERIFIED FINDINGS (skeptic pass, 2026-06-11)

Verification base: /Users/lume/ClawDnD-val, HEAD a245a2c (f24a102 is an ancestor, 6 commits back; the interval touches level-up backfill/#624, VM QA lane, narration heartbeat/#749 — none of unit-05's surfaces; every cited file:line re-checked against HEAD and matches). Method: repro R1–R5 re-run on HEAD (`uv run python /tmp/engine-audit/u05_repro.py`, throwaway state dir) — all five reproduce verbatim; F5-3 payload independently recomputed from world.json through the exact present_events projection = **6,569 B, byte-identical to the auditor's snapshot figure**; F5-10 field incidence independently measured across 176 campaign snapshots (play-state/** + qa/transcripts) = **59/173 meeting beats bind Raphael/The Emperor/Withers (34% ≈ the predicted 3/9)** — this measurement OVERTURNS an earlier skeptic draft's "zero field incidence" downgrade rationale (that draft's glob missed the snapshot layout). Dup re-scan against all 146 open issues incl. #745–#758: no owners.

**Verdict: 9 confirmed, 1 corrected (F5-10: fix-spec repaired, severity P2 kept), 0 refuted.**

---

### F05-1 — Rule-of-three quest evolution is unreachable; the DM skill documents a call shape that TypeErrors — **CONFIRMED [P1|high|S]**
- Root verified on HEAD: `complete_quest(campaign_id, quest_id, status="completed")` only (server.py:7768); SKILL.md:90,96 + reference/living-arcs.md:21 instruct `complete_quest(..., evolves_to=…, callback_in_days=N)`; repro R1 → `TypeError: complete_quest() got an unexpected keyword argument 'evolves_to'`. `_maybe_schedule_quest_evolution` (server.py:7730-7763) is internally correct (status gate, `evolves_from:<id>` note idempotency, note-not-thread_id channel).
- Cheapest-disproof attempts FAILED (finding stands): grep `evolves_to|callback_in_days` over server.py tool signatures, content.py, questgen.py, all world.json → zero production writers anywhere; no `update_quest`/kwargs lane; add_quest takes title/description/giver_id/location_id/objectives only (server.py:7698-7705). Only tests set the field, by direct model mutation (tests/test_dashboard.py:33-39) — the tool seam is untested.
- Fix-spec vs invariants: PASSES — optional params defaulting to today's behavior (additive), assignment under the existing campaign_lock before the 7783 helper call (sole-writer), no model/wire change. Edge cases check out against helper code (failed → store-don't-schedule; note guard keeps first echo on re-complete).
- Severity: P1 holds (not P0 — no gate breaks today, but the skill's NAMED saga mechanism is dead in 100% of campaigns and the director nags an impossible action every beat in both real quest-completing duo runs).
- dup: new (#59/#71/#73 director epics are extensions, not this defect).

### F05-2 — set_quest_status and complete_objective auto-resolve never schedule evolution — **CONFIRMED [P2|high|S]**
- Root verified: `_maybe_schedule_quest_evolution` has exactly one call site (server.py:7783). set_quest_status's tracked-quest branch (server.py:~8630-8650 — comment "Mirror complete_quest" mirrors only the milestone XP) and complete_objective's all-done branch (server.py:7863-7872) flip status + pay XP, no helper call. Repro R2/R3 re-run: `evolution scheduled? False` on both.
- Fix-spec: PASSES (call helper after status flip in both branches, mirror the 7795-7800 return shape; note guard makes all three verbs mutually idempotent; no new state).
- dup: new.

### F05-3 — All authored Events are trigger "manual" → every unresolved event's full prose rides EVERY beat (~1.6K tokens at cold open) — **CONFIRMED [P1|high|M]**
- Root verified: `trigger_holds` returns True unconditionally for "manual" (events.py:49-50); `present` returns every unresolved holding event, no cap (events.py:66-77); scene_context — mandated "use this every beat" (server.py docstring + SKILL.md step 1) — embeds `present_events` (server.py:9128) with full prompt + options projection (server.py:8369-8384); authored events: baldurs-gate 5/5 manual (prompts 737–943 chars), tidal-commonwealth 2/2 manual (measured from world.json this pass); living-arcs.md:41-43 promises "most beats it returns nothing, and that's correct".
- Measurement REPRODUCED independently: projecting BG's 5 events through the exact tool projection = **6,569 bytes** — byte-identical to the auditor's snapshot measurement. ≈1.6K tokens every beat from beat 1, incl. Raphael's bargain at minute one.
- Fix-spec vs invariants: PASSES — (a) content triggers use only the existing tested vocab (flag_set/day_reached/reputation_at incl. fall direction); (b) engine valve is read-path-only + additive (`manual_queued` new key; single-manual worlds identical; `resolve_event` looks up `c.events.get(event_id)` directly — server.py:8410 — so a queued-but-unsurfaced manual event stays resolvable by id; frozen wire/model contracts untouched).
- Severity: P1 holds — measured, always-on, every beat of every BG campaign; input token mass is the engine's main latency lever and the design promise is directly contradicted. Latency component is prefill (modest); the pacing/mis-staging component is the bigger story-craft risk — both real duo runs carried 3–5 unresolved event payloads all session.
- dup: new — #753 is "define a GUI-loop latency BUDGET" (a measurement work item), adjacent, not this defect.

### F05-4 — resolve_scene_debt does not suppress re-detection; resolved debts re-surface and can never be re-resolved — **CONFIRMED [P2|high|M]**
- Root verified: `detect()` registry (scene_debt.py:407-433) never reads `c.scene_debts`; `director.compute` (director.py:90) and `get_scene_debts` (server.py:~8716) call raw detect; `resolve_scene_debt` persists then refuses re-resolve with "already resolved" (server.py:~8748-8750). Deterministic `_debt_id` (scene_debt.py:19-25) guarantees the same structural fact re-detects under the SAME id. SceneDebt has no resolution-day field (models.py SceneDebt: id/kind/subject/detail/severity/evidence/resolved/resolution_evidence only). Repro R4 re-run: resolved debt still in live_debts AND director advisory; re-resolve blocked.
- Test suite ENSHRINES the broken behavior: `test_resolve_already_resolved_returns_gracefully` (test_scene_debt.py:431-445) asserts the refusal; nothing anywhere asserts suppression. The fix must amend that test.
- Fix-spec vs invariants: PASSES — `resolved_day: int = -1` additive on _StrictModel (old snapshots round-trip); `scene_debt.live(c)` keeps detect() pure; writes stay under lock; per-kind suppress/snooze policy reasonable; changing the early-return (refuse only when not-live, else update record) is an acceptable advisory-tool behavior change, not a frozen contract.
- Severity: P2 on mechanism; field incidence of the verb is 0 (never used in real play — consistent with it being useless), so bite is latent but structural the moment it's used (crowd-out of the `_TOP_N=3` slots, director.py:20, is directly assertable).
- dup: new.

### F05-5 — choice_without_outcome unresolvable; director nudge names nonexistent `update_decision` — **CONFIRMED [P2|high mech/med field|S]**
- Root verified: nudge string at director.py:43 — the ONLY `update_decision` reference in the repo (other grep hits are stale worktree copies of the same line); `record_decision(chosen: str = "")` (server.py:8309) makes the pending state producible via the public contract; decisions append-only (no mutation path); detector scans each Decision independently with `d.options and not d.chosen.strip()` at high severity (scene_debt.py:216-237). Repro R5 re-run: debt live, severity high, `hasattr(server,"update_decision")` → False.
- Fix-spec: PASSES — narrow mutate-under-lock tool (chosen/rationale only), nudge text fix; fuzzy later-decision clearing correctly rejected (fiction-matching violates gates-read-engine-values).
- Severity: P2 kept (permanent high-sev top-3 occupant + prescribed action that errors) with the auditor's own latency caveat: field incidence currently 0 — latent, "a matter of time" given the optional-`chosen` skill contract.
- dup: new.

### F05-6 — Faction-arc progression is a dark loop: gauges evaluated only by join_faction/check_faction_arcs; beat loop and skill never call them; detectors only report already-flipped stages — **CONFIRMED [P2|high mech/med field|M]**
- Root verified: `faction_arc_mod.evaluate` call sites: exactly server.py:8161 (join_faction, once) + 8215 (check_faction_arcs); nothing else (7493 is companion_arc.evaluate). scene_context bundles durable/director/events/companion_arcs only (server.py:9126-9130). grep `check_faction_arcs` in skills/ agents/ commands/ → 0 hits. Both detectors are explicitly already-flipped-only: scene_debt.py:364 (`s.status == "available"`) and faction_arc.detect_rank_available (faction_arc.py:90-91: "it does not flip locked->available; it only reports stages already in those states"). A locked stage whose gate HOLDS is invisible on every surface. No compensating caller found (downtime/world_tick don't evaluate arcs).
- Fix-spec vs invariants: PASSES with one CORRECTION kept — `stage_gate_holds` exists (faction_arc.py:43) and is pure, so the read-only earned-but-locked detection is feasible and preserves advise-not-act + evaluate's sole-flipper role. Correction: the detector must label earned-but-locked DISTINCTLY from available (e.g. `earned_locked_stage_ids` in evidence + nudge "rank-up EARNED — call check_faction_arcs then play it"), never put a locked stage id in `available_stage_ids` — otherwise the advisory misstates engine state. Rejecting check_faction_arcs-in-scene_context (a second writer in the read-mostly bundle) is right, though note check_companion_arc already persists arc progress there, so the precedent is contested.
- dup: new.

### F05-7 — quest_stalled ignores the engine's own progress verbs; a quest added late is flaggable immediately — **CONFIRMED [P2|high|M]**
- Root verified: (a) `_quest_has_decision_callback` (scene_debt.py:91-103) scans only Decision text (summary/rationale/chosen/options) for quest id/title; `complete_objective` (server.py:7841-7878, read in full) mutates only objectives/status/XP — records no Decision, stamps no day. (b) `_detect_quest_stalled` (scene_debt.py:157-182) guards only `c.day <= QUEST_STALL_DAYS` globally; Quest carries zero temporal fields (models.py Quest: id/title/description/status/objectives/completed_objectives/giver_id/location_id/evolves_to/callback_in_days/milestone_awarded — verified). Module concedes the proxy in its own comments (scene_debt.py:29-36).
- Qualifier (not a refutation): the day-7 quest flags immediately only when no decision text matches within the window — a DM who records an acceptance decision naming the exact title gets grace; DMs rarely echo exact titles, never `quest_xxxx` ids.
- Fix-spec vs invariants: PASSES — `Quest.last_progress_day: int = -1` additive; stamped under lock by the four verbs; detector reads an engine-mutated value (better invariant hygiene than text-scanning); Decision-text path kept as old-snapshot fallback.
- dup: new.

### F05-8 — Director `_nudge` lacks cases for thread_no_payoff / faction_rank_available; fallback truncates mid-instruction — **CONFIRMED [P3|high|S]**
- Root verified: director.py:25-66 handles exactly 6 of 8 detect()-producible kinds (hook_untracked, quest_stalled, choice_without_outcome, due_consequence, thread_pressure, npc_introduced_silent); the other two hit `f"{debt.kind}: {debt.detail[:80]}"` (director.py:66). thread_no_payoff's detail (scene_debt.py:~205) reads "…consider a callback (set evolves_to) so it lingers…" — `[:80]` cuts exactly before the verb, matching the measured duo-run advisory. Bonus claim verified: due_consequence's evidence dict has consequence_id/trigger_day/campaign_day/overdue_days/note — NO `text` (scene_debt.py:262-268); the nudge prints `note` (director.py:48-50), often empty or an internal `evolves_from:` tag.
- Fix-spec: PASSES (wording coupled to whatever F05-1 ships — correct dependency; lifting the fallback truncation is safe, detail is one line).
- dup: new.

### F05-9 — npc_introduced_silent flags dead characters — **CONFIRMED [P3|high mech/low field|S]**
- Root verified: social_check accepts kind ∈ (npc, monster, companion) and flips `the_npc.met = True` unconditionally (server.py:6504-6515); detector (scene_debt.py:308-346) filters kind ∈ (npc, monster)/location/met/memory but never `ch.dead`/`current_hp`. A slain, memory-less monster at the current location yields "they haven't spoken yet."
- Field incidence: 3 such debts across snapshots, none dead (auditor's own caveat) — P3 right-sized. Fix-spec (two-line liveness guard): PASSES.
- dup: new.

### F05-10 — Prelude "meeting" beat binds a uniformly-random roster NPC — 3-in-9 chance of Raphael/The Emperor/Withers in shipped BG — **CORRECTED (fix-spec repaired; severity P2 KEPT) [P2|high mech, med impact|S]**
- Mechanism verified: `_build_prelude` does `rng.choice` over all kind ∈ (npc, companion) minus easter-egg exclusions (questgen.py:189-191); BG roster = 10, only npc-claudan easter_egg=true, and roster entries declare NO kind field — content.py:1755 **hardcodes `kind="npc"`** at ingest, so the pool is 9 uniform with 3 villain/deity-tier entries.
- **Field incidence MEASURED this pass (new evidence, strengthens the auditor)**: across 176 campaign snapshots with preludes (play-state/** + qa/transcripts), 173 meeting beats → npc-raphael 23, npc-withers 22, npc-the-emperor 14 = **59/173 (34%)**, matching the predicted 3/9 uniform draw. This is NOT latent: a third of every real seeded BG campaign — including gate runs — opens on a villain/deity meeting suggestion, and SKILL.md:28,89 mandates weaving `get_prelude` at every cold open. (An earlier skeptic draft downgraded to P3 on "zero observed field incidence" — that claim was a measurement artifact of a wrong glob; it is withdrawn. Auditor's P2 stands; impact confidence med because the DM may reframe — the meeting note text is generic and all three NPCs do canonically appear early in BG3, so the failure mode is tonal mis-staging, not canon breakage.)
- **Fix-spec CORRECTION (infeasible branch removed)**: "prefer kind=='companion' when any exist" is dead code at seed time — the content schema has no `kind` field and ingest hardcodes "npc", so no world can ship a companion-kind roster entry, and the proposed test "a roster WITH a kind=companion entry always binds a companion" cannot be authored through content. Drop that branch (or scope it as a future content-schema extension). Actionable spec: (a) additive roster flag `prelude_meetable: false` (default true, parsed alongside easter_egg) on Raphael/The Emperor/Withers — excluded from the meeting pool only, still valid quest targets; (b) optional: prefer spine-grievance token overlap (existing `_toks`/`_best_overlap` machinery) over uniform choice. Old worlds unchanged (additive).
- Test: ~100 seeds on BG → meeting ref_id never ∈ {npc-raphael, npc-the-emperor, npc-withers, npc-claudan}; flagless world's distribution unchanged.
- dup: new.

---

## CLEAN-VERIFIED (skeptic spot-checks)
Independently re-verified from source this pass: #2 resolve_event idempotency (server.py:8413-8421 — resolved short-circuit, no save, no ripple); #3 trigger semantics incl. negative-threshold fall direction, unknown-trigger and missing-faction degrade (events.py:48-63); #6 evolution-helper internals incl. note-not-thread_id rationale (server.py:7744-7763); #7 milestone single-award latch on all three verbs (server.py:7789, 7866-7869, set_quest_status branch); #11 deterministic debt ids (scene_debt.py:19-25); #13 get_scene_debts/get_campaign_director read-only (no save) + scene_context sequential-never-nested lock note (server.py:9116-9120, verified in docstring + code). Items #1, #4, #5, #8, #9, #10, #12, #14, #15 accepted on the auditor's cited tests (test_event_parley_layer3.py, test_faction_arcs.py, test_questgen.py, 152-snapshot probe) — no contradicting evidence found.

## TALLY
unit 05: **9 confirmed, 1 corrected (F05-10), 0 refuted.**
- F05-10 correction: companion-kind preference branch of the fix is unreachable from content (content.py:1755 hardcodes kind="npc") — replaced by the `prelude_meetable` flag + overlap preference; severity stays P2 on newly measured 34% field incidence (59/173 real meeting beats bind Raphael/Withers/Emperor).
- Cross-cutting: all cited file:line refs hold on HEAD a245a2c; repro R1–R5 re-run green; F5-3 payload byte-reproduced (6,569 B); F5-4 fix must amend test_scene_debt.py:431-445 (it enshrines the refusal); F5-6 fix must label earned-but-locked distinctly from available; F5-8 due_consequence evidence-omits-text bonus verified.

════════════════════════════════════════════════════════════════
## UNIT 06 — VERIFIED REPORT (verbatim from unit-06-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 6 — COMPANIONS: SKEPTIC-VERIFIED report

Verifier pass on /Users/lume/ClawDnD-val @ a245a2c (main; brief said f24a102 = an ancestor —
all findings re-checked on the NEWER tip, none invalidated by the intervening commits).
Method: every cited file:line opened; both runnable repros re-executed fresh; the tool-call
census re-run independently (matches the auditor exactly: 277 transcript files, 2,903
tool_use events; companion_advise 54, recruit 38, social_check 30, scene_context 14,
start_combat 5, create_character 111, load_canon_character 78, and 0 for camp_scene /
record_camp_beat / check_companion_arc-direct / companion_suggest_action / set_companion_arc /
all quest-arc tools / adjust_attitude / set_attitude / long_rest / short_rest); snapshot
census re-run (172 snapshots, 20 companions: 20/20 arc=None, 20/20 dossier=None, 20/20
attitude_value=0, 0 camp-beat records). Dedup re-checked against the 146-row open-issues set
incl. #748–#758 (none of the in-flight reliability issues touch companions). Skill docs
checked as a disproof angle (combat.md:104 and living-world.md:16 DO document
adjust_attitude/camp_scene — so corpus non-use is partly adoption, noted per finding).

**Verdict: 8 confirmed, 3 corrected (F6-4 boundary nit, F6-10 false sub-claim, F6-11 reach),
0 refuted. No severity inflation found worth a downgrade; no fix-spec violates the engine
invariants (sole-writer, additive round-trip, engine-rolls, no fiction-reading triggers).**

---

### F6-1 — CONFIRMED [P1|high|S] Two of three companion-creation paths seed NO arc and NO dossier
- Verified on main: `create_character` (server.py:1326–1421) has no companion arc/dossier
  branch; `load_canon_character` (server.py:2342+) seeds the dossier via `_coerce_dossier`
  (~2455) but never an arc; the seeding block lives only in `recruit_companion`
  (server.py:1755–1782, comment records the original QA hit). `start_adventure` docstring
  (server.py:468–473) prescribes the unseeded create_character path.
- Disproof attempts: (a) "intended flow is load_canon → recruit" — refuted by the snapshot
  census: 20/20 live companions arc=None/dossier=None (re-run, matches), and create_character
  is the dominant path (111 vs 38 calls). (b) No open issue owns the seeding gap (#616 is the
  UI ladder; #58 is the epic). (c) No test asserts arc/dossier on the create/load paths.
- Fix-spec check: helper `_seed_companion_operational_state` called at 3 sites, None-guarded
  (ending-seeded arcs never overwritten — same guard discipline as today), all sites hold
  campaign_lock, additive (None stays valid on old snapshots). Invariant-clean. KEEP AS SPECIFIED.
- dup: new. Severity P1 stands (story_craft keystone structurally inert on the dominant path;
  not P0 — no gate breaks today).

### F6-2 — CONFIRMED [P1|high|M] Approval gauge has no organic mutation path; approval_likes/dislikes are dead fields
- Verified on main: only attitude_value writers are set_attitude (server.py:6305),
  adjust_attitude (6331), social_check influence ±15/−10 (6539). record_decision
  (8305–8352) writes decisions+flags only; resolve_event ripples faction rep + flags.
  `grep approval_likes|approval_dislikes servers/` → models.py:437–438 + tests ONLY (zero
  engine readers; rich authored data exists in content/worlds/baldurs-gate — Minsc, Astarion,
  Karlach, shadowheart.json etc. — all unread). Census re-run: adjust/set_attitude 0 calls
  ever; all 20 snapshot companions at 0.
- Framing correction (kept, severity unchanged): part of the starvation is DM ADOPTION of
  tools that already exist and are documented (combat.md:104 names adjust_attitude) — but the
  engine halves are real: no event-carried approval delta (record_decision's own docstring
  sells the one-step choice→consequence path yet moves only flags, never the gauge), and
  authored approval causes with zero readers. The per-beat gate-distance surfacing (F6-2.3)
  is the adoption lever.
- Fix-spec check: optional clamped `approval:[{companion_id,delta,reason}]` on
  record_decision/resolve_event (engine writes under the same lock — sole-writer holds);
  attitude_log additive default [] (round-trips); explicitly REJECTS dossier-tag NLP matching
  (invariant 3 — correct call). points_to_next_gate already computed by _camp_arc_summary
  (server.py:6857–6868). Invariant-clean. KEEP.
- dup: enriches #593 + #612/#613 (log models + mutator-site logging are THOSE issues —
  implement against them); the mutation-moments wiring + scene_context gate-distance
  surfacing are new. #345 only covers the UI gauge display. #614/#615 are the UI panels.

### F6-3 — CONFIRMED [P1|high|S] companion_advise (the only live companion surface, 54 calls) ignores dossier, gauge, and arc
- Verified on main: `deliberate` (companion.py:230–259) reads exactly
  name/voice_id/personality + callbacks; wrapper (server.py:6842–6852) passes
  `(comp, situation, callbacks)` and nothing else; zero `companion_dossier`/`attitude` tokens
  in companion.py (grep re-run). Census re-run: 54 calls vs 0 for every depth tool.
- Fix-spec check: optional `dossier`/`standing` args (pure module stays pure, dossier-None
  path byte-identical = additive guarantee), stance hint derived from the engine-computed
  attitude band (reads only the gauge — invariant-safe), approval causes in the RETURN
  payload for human judgment (the correct division of labor with F6-2). Read-only tool stays
  read-only. Invariant-clean. KEEP. Highest ROI in the unit — agreed.
- dup: new.

### F6-4 — CORRECTED (boundary) [P2|high|S] Betrayal telegraph dead zone — agendas with threshold ≤ −40 never warn
- Reproduced fresh on main (threshold × attitude sweep): threshold −50 → NO warning at any
  attitude (−10…−60 all silent); threshold −30 → warns ONLY at −31..−40, silent again at −41
  while live/unfired; threshold −20 → warns −21..−40, silent at −41.
- CORRECTION: the never-warn dead zone is threshold ≤ **−40**, not ≤ −41 — at value=−40 the
  conditions (`av ≥ ATTITUDE_WARN_LOW=−40` at companion_arc.py:244 AND `av < value=−40` at
  246) are already disjoint. Root cause exactly as cited: absolute band [−40,−20]
  (constants at companion_arc.py:116–117) intersected with a content-defined threshold.
- Disproof attempt: "silence below −40 is intended ('already deep-red / near-snap')" — fails
  for deep thresholds: a −50 agenda gets NO telegraph at ANY attitude, defeating the
  function's own stated purpose ("foreshadow the turn instead of springing it from nowhere",
  222–231). The −41-silence for shallow agendas is at minimum a contradiction with
  "never go silent while live and unfired" given no other advisory exists below the band.
- Fix-spec check: threshold-relative band, advisory-only, no mutation, constants stay
  module-level. Note repeated warnings per beat are consistent with today's in-band behavior
  (the EXACTLY-ONCE contract covers unlock/fire events, not warnings). Invariant-clean. KEEP.
- dup: new.

### F6-5 — CONFIRMED [P2|high|M] Camp/banter pillar unreachable, never rotates prompts, structurally starves pair banter
- Verified on main, all three legs: (a) the only camp_hint is long_rest's (server.py:6073);
  long_rest census 0; scene_context durable has no camp affordance (8985–9003 shows
  gates/agenda flags only); camp_beat records 0 across all 172 snapshots (re-run).
  (b) `_dossier_hooks` hard-indexes `camp_prompts[0]` (companion_banter.py:69) with an anchor
  slug from constant tags (82) → constant cooldown key → same prompt forever after cooldown;
  prompts 1..3 are dead content. (c) camp_scene passes `max_beats=len(companions)`
  (server.py:6912); solo priorities 50–90 vs pair `40+len(tags)` ≤ 43
  (companion_banter.py:118) sorted by -priority (143) → the first camp of any campaign
  mathematically cannot contain a pair beat.
- Fix-spec check: reach hints are advisories on existing tools + a pure read of
  camp_beats.records (engine-mutated — invariant-safe); rotation index k = count of prior
  solo records (deterministic, no rng — scheduler stays pure); pair-slot reservation additive.
  Invariant-clean. KEEP.
- dup: enriches #592 (epic covers rest/supplies; the rotation/reach/interleave defects are
  new specifics; adjacent cluster #609–#611 doesn't own them).

### F6-6 — CONFIRMED [P2|high|S] Heal suggestion splits on spell-name case — "cast None" + false "no spell slot"
- Reproduced fresh on main with `spells_known=["healing word"]` + 1 free slot:
  heal branch → `{'spell': None, 'reason': '...cast None on PC before trading blows.'}`;
  aid_downed → `{'spell': None, 'reason': "...there's no spell slot to heal — stabilize..."}`
  (false — slot free, heal known). Title-Case control returns `spell='Healing Word'`,
  bonus_action=True, then_attack chained — confirming the split is purely case.
  Root cause exact: `_can_heal` lowercases (companion.py:47–52); `_best_heal_spell` matches
  Title-Case `_HEAL_PRIORITY` case-sensitively against the raw set (74–75); spells.py is
  case-insensitive everywhere else (21,25,52) — companion.py is the sole outlier.
- Exposure caveat (severity kept P2): companion_suggest_action has 0 corpus calls TODAY, so
  no player has seen this yet — but the bug also lives under `InProcessCompanion.take_turn`
  (the Tier-1 provider path) and becomes every-companion-turn-visible the moment F6-8.3
  inlines suggestions into next_turn. Fix is S and pure-module; fix before wiring F6-8.
- Fix-spec check: lowercase-normalize match, return canonical Title-Case, split
  no-spell vs no-slot wording. Pure module. Invariant-clean. KEEP.
- dup: new.

### F6-7 — CONFIRMED [P2|high|S] Mid-run recruit XP — no backfill + guaranteed false companion_xp_synced_on_award WARN
- Verified on main: recruit_companion (server.py:1689–1795) contains zero `.xp` writes
  (grep re-run: 0 hits) and co-locates the recruit in the same call (~1790). The WARN
  predicate (qa/assert_behavioral.py:629–646) is `kind==companion AND not dead AND
  location_id==current AND xp==0` with `pc_xp_max>0` — its own comment (625–628) claims the
  co-location scope-guard protects "a companion that joined mid-run", but recruit GUARANTEES
  co-location, so the guard guards nothing for exactly the named case. PR #739 (merged,
  8019fdf "co-locate AND co-earn") fixed award-time shares only — `_party_xp_recipients`
  (server.py:239+) is award-seam routing, no join-time backfill.
- Fix-spec check: xp-mode backfill `ch.xp = min(living players' xp)` when recruit xp==0,
  under the lock recruit already holds; milestone-mode level default to PC total_level when
  caller left level=1; explicit builds win; additive return field. After backfill the
  existing WARN becomes sound UNCHANGED — elegant. One refinement noted for the implementer:
  a 900-xp backfill onto level-1 classes will immediately signal levels-available; the spec's
  test expects exactly that (genre-correct catch-up) — make it explicit in the PR. Invariant-clean. KEEP.
- dup: new (post-#739 edge; #696 is provider-parity gate alignment, different concern).

### F6-8 — CONFIRMED [P2|high|M] Companion combat participation unenforced + unobserved; tactical aid never used
- Verified on main: start_combat (server.py:3013+) builds the order strictly from passed ids
  (3040–3056), no party diff, no omission advisory — while the advisory PATTERN exists right
  there (extra_attack_reminder 3088, auto-outlook 3117–3134). assert_behavioral.py companion
  checks are dialogue/location/XP only (grep re-run: 122–185, 592–646 — no combat-presence
  join). combat_start events DO persist the combatant list (`_log_combat_event`, 3112–3116) —
  the proposed gate WARN is implementable from existing logs. next_turn HAS a turn_brief
  (3645, `_turn_brief` 3330) — the inline-suggestion leg is implementable as specified.
  Clean-verified cross-check: the PC-skip guard (3484–3507) covers kind=="companion" — the
  engine is correct once they're IN; inclusion is the unenforced half. Census: start_combat 5,
  companion_suggest_action 0.
- Fix-spec check: all three legs advisory/read-only/additive; inlining the pure
  suggest_action into turn_brief kills a round-trip (latency-doctrine-consistent). NOTE:
  fix F6-6 first — leg 3 wires the case-buggy suggestion into every companion turn.
  Invariant-clean. KEEP.
- dup: new; cross-link #166 (the player-side DM-adherence cluster — this is its companion
  sibling; same fix grammar: per-turn surfacing + behavioral assertion).

### F6-9 — CONFIRMED [P3|high|S] suggest_action advises an unconscious companion to revive/stabilize ITSELF
- Reproduced fresh on main: companion at 0 HP → `aid_downed` targeting itself, reason
  "...stabilize themselves (Spare the Dying, or a Medicine check)" (and with a slot+heal the
  Title-Case variant says "cast X to revive themselves now"). Unconscious creatures can't
  act — RAW-illegal guidance. Behavior is test-LOCKED as intended (test_companion.py:55–63
  `test_suggest_aid_downed_self` — confirmed present), i.e. designed-but-unexamined, exactly
  as the auditor characterized. next_turn separately reports the owed death save, so the two
  tools disagree on the same turn.
- Fix-spec check: self-down → `action: "death_save"` with engine-rolls pointer (death_save
  tool) — engine-rolls invariant respected; other-ally aid unchanged; update the locking
  test. Invariant-clean. KEEP.
- dup: new.

### F6-10 — CORRECTED (sub-claim) [P2|high|M] CompanionQuestArc engine-complete but content-unreachable
- CONFIRMED core (all re-verified on main): `grep companion_quest content.py` → 0 hits;
  `grep -rln companion_quest_arcs content/` → 0 files; tool cluster 0 corpus calls;
  `_camp_arc_summary` (6857–6868) + scene_context.durable.companions (8985–9003) surface
  gates/flags only — no quest-arc mention anywhere DM-facing.
- CORRECTION — the "can't even be authored" sub-claim is FALSE as stated: personal_quest
  GATES are authored in 5 shipped overlays today (gortash-tyranny, dark-urge-bhaal ×2,
  illithid-ascension, second-tide-rising) — as note-only bond beats with NO quest_arc_id
  (model default "", so `_unlock_companion_quest_arc` early-returns None and they unlock
  fine). The true, narrower claim: a LINKED gate has no content path to a resolvable target
  (no arc loader exists), AND content.py's companion_seeds path never runs
  `_validate_companion_arc_quest_links` (that validator fires only at set_companion_arc,
  server.py:7526, and quest-arc replace, 7549) — so an author who DID write a quest_arc_id
  into an overlay gets a silently-seeded dangling link that lands in F6-11's forever-error.
- Fix-spec ADDITION (required): the new loader must validate gate→arc links with the same
  degrade-not-abort discipline (drop the link or the gate, log it) — otherwise F6-10's
  loader is the very thing that arms F6-11 in production. Seed arcs BEFORE companion_seeds
  arcs as specified. Otherwise invariant-clean (additive, default {} round-trips). KEEP.
- dup: enriches #58 (epic); the loader/exemplar/surfacing spec is new.

### F6-11 — CORRECTED (reach) [P3|med|S] Broken personal_quest gate link re-reports its error EVERY beat, forever
- CONFIRMED code-certain on main: companion_arc.evaluate (companion_arc.py:340–344) appends
  the error and `continue`s with the gate still locked and nothing marked — the identical
  error regenerates every evaluate; check_companion_arc includes the companion whenever
  companion_quest_unlocks is non-empty (server.py:7492–7500); scene_context delegates to
  check_companion_arc every beat (9128). Violates the module's EXACTLY-ONCE contract
  (companion_arc.py:10–14) for the error case.
- CORRECTION — reach is LATENT today: no shipped content carries a quest_arc_id (grep: 0),
  set_companion_arc validates links at write time (7526), and quest-arc replacement is
  guarded (7549) — so the only current mint paths are hand-built/legacy snapshots. The
  defect goes LIVE the day F6-10's loader ships. Recommendation: fold this fix into the
  F6-10 work item (plus the loader-side link validation added above) rather than a separate
  backlog row.
- Fix-spec check: both options additive/round-trip-safe. Skeptic's note on the auditor's
  preferred (a) (unlock anyway + unlink_error): it changes gate semantics (bond beat without
  the promised quest); option (b) (one-shot `link_error` field on ArcGate, additive default
  "") preserves the wire shape and is the smaller surprise — genuinely owner taste, so
  confidence med stands. KEEP at P3/med.
- dup: new (fold into F6-10 execution).

---

## CLEAN-VERIFIED — skeptic spot-check results (all 12 upheld)
1. Arc stage-machine engine-enforced — RE-VERIFIED (evaluate mutates under check_companion_arc's
   lock+save, server.py:7483–7502; EXACTLY-ONCE docstring + gate-flip-only reporting read).
2. Snap-curve #142 / M2 validator — RE-VERIFIED (models.py:249–256 `_require_threshold` raises
   on missing value for attitude_below/day_reached).
3. Engine rolls + PC-skip guard covers companions — RE-VERIFIED (server.py:3484–3507).
4. suggest_action tactics (peril-override etc.) — RE-VERIFIED in code (companion.py:178–212);
   tests present.
5. recruit_companion hygiene — RE-VERIFIED (guarded death-clear, None-guards, co-location;
   server.py:1742–1795).
6. Camp scheduler purity/safety — RE-VERIFIED (camp_scene lock-free pure read; scheduler
   sorted/deterministic, companion_banter.py:130–144); record-path guards trusted to tests.
7. CompanionQuestArc API validation — RE-VERIFIED (server.py:7405–7430 + 7526/7549).
8. #739 XP-share — RE-VERIFIED (`_party_xp_recipients` server.py:239+, de-facto-companion
   rationale in docstring).
9. social_check semantics — writers verified (6509–6553); READ-no-move trusted to tests.
10. scene_context lock discipline — RE-VERIFIED (sequential never-nested flock, 9116–9128).
11. #612–#616 scaffold-only — RE-VERIFIED (zero attitude_log/reputation_log tokens in
    models.py/server.py).
12. No refuted-latency-pattern proposals — RE-VERIFIED (all latency notes are token-mass or
    round-trip shaped).

## Ordering note for synthesis
F6-6 before F6-8 (leg 3 wires the buggy surface into every companion turn). F6-11 folds into
F6-10. F6-1 → F6-3 → F6-2 is the S+S+M spine the unit verdict hangs on — verified sound.

════════════════════════════════════════════════════════════════
## UNIT 07 — VERIFIED REPORT (verbatim from unit-07-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 7 VERIFIED — MEMORY / RECAP / FTS / SCENE_CONTEXT (skeptic pass)

Repo: /Users/lume/ClawDnD-val @ a245a2c (actual main HEAD; the tasking said f24a102 which does not exist locally — auditor's SHA matches disk). Read-only verification 2026-06-11.
Method: every cited file:line re-opened on main; measurement probes RE-RUN where cheap (recap_probe, kind_probe2, measure_sc, tool_census, tool_census2, a fresh s10 start_session/session_recap probe); dup_status re-checked against the full /tmp/engine-audit/open-issues.txt (146 lines, incl. #748–#758, #593, #17); fix-specs checked against the engine invariants (sole-writer, additive `_StrictModel` round-trip, engine-rolls, frozen wire contracts).

Verdict summary: **9 confirmed as written, 4 confirmed with corrections (F7-1 fix-spec, F7-4 scope, F7-8 root-cause nuance + digest spec, plus 2 severity downgrades F7-2 P1→P2 and F7-9 P2→P3), 0 refuted.** The auditor's measurements are unusually reproducible — F7-1/2/3/4/7/13 all reproduced byte-for-byte on main today.

---

### F7-1: Recap and FTS ledger contaminated by combat/system bookkeeping rows — CONFIRMED (P1), fix-spec CORRECTED
**Verification:** recap.py:18 `_STORY_KINDS` includes "combat"; ledger.py:191 indexes combat+system as kind="events" with who=speaker (ledger.py:196). `_log_combat_event` (server.py:151-153) stamps `payload.schema == "clawdnd.combat_event.v1"` (server.py:130). RE-RAN recap_probe.py on main: recap opens with "Tough 1 takes 5 force damage… Turn advances to Tough 2…" exactly as claimed; recall('Rolan') is actually **4 of top 6** bookkeeping (worse than the audit's 3/6). Blast radius slightly WIDER than audited: `companion_advise` (server.py:6851) also pulls callbacks from the same contaminated index.
**Correction to the fix spec:** the option "drop 'system' from the indexed tuple entirely" is WRONG — the auditor's grep covered engine-authored writers only, but log_event's docstring (server.py:7202) and **SKILL.md:47 explicitly prescribe DM-authored `system` rows via persist_beat *so they feed recall*** ("a terse mechanical/`system` note for recall… Loose prose only feeds recall if you log it"). Dropping the kind breaks that documented contract. Corrected spec: (1) recap.format_recap keeps kind=="combat" only when payload is None/lacks the combat-event schema (as audited — sound); (2) ledger.backfill skips rows with the combat-event payload schema AND skips the two engine session markers by exact-prefix match ("Session N began"/"Session ended.") — same exact-match discipline as #749 — keeping other system rows indexed. Real-data check: all 149 system rows in 810 are markers (0 DM-authored today), so observable behavior is identical, but the spec must not foreclose the documented path.
**Invariants:** derived-index-only change, no schema change — clean. dup: new (re-confirmed: #749 is merged in HEAD a245a2c and exact-matches only wrapper-heartbeat lines; no open issue owns combat-row contamination). Severity P1 holds: the cold-open `previously_on` is consumed by every run's setup turn and the contamination is live and reproduced.

### F7-2: recall_npc split-brain (dialogue by NAME, facts by ID) — CONFIRMED, severity DOWNGRADED P1→P2
**Verification:** ledger.py:130-143 `WHERE who = ?` exact; backfill writes who=e.speaker for dialogue (196), who=ch.id for npc_facts (199). RE-RAN: `recall_npc("Withers")` → 2 dialogue rows only; `recall_npc("npc-withers")` → 3 facts only. Exactly as claimed. SessionLogEntry.speaker is "character id or name" (models.py:1077). Fix spec (query-time `WHERE who IN (id, name)` via read-only load_campaign, fall back to single key) is invariant-clean and the rejected-alternative reasoning (backfill normalization breaks ad-hoc speakers) is sound.
**Severity correction:** the auditor's own F7-7 census (re-run, exact: recall_npc = 0 across 277 QA + 68 play-state transcripts) proves zero behavioral exposure TODAY — nothing in any current run is degraded. The lean production prompt mandates the call, so this becomes P1 the moment F7-7's adoption fix lands — it is a hard prerequisite for that work, but by the gate definitions it is not P1 today. **P2**, depends_on noted as "must land with/before F7-7 adoption". dup: new.

### F7-3: scene_context re-sends full event prompts every beat (~1.6K tok/beat) — CONFIRMED (P1)
**Verification:** RE-RAN measure_sc.py on main: events = 6,454 B (~1,613 tok), 78% of base bundle / 38% of the lean read (17,033 B) — byte-identical to the audit. events.py:34-49 `trigger_holds` returns True unconditionally for "manual" (read directly); present() serializes full Event records; server.py returns them verbatim. The "every beat" claim HOLDS for production: lean mode is default (CLAWDND_LEAN_BEATS=1) and the lean system prompt (qa/lib_beat_driver.sh:421-427) makes scene_context the mandatory FIRST action of every beat; SKILL.md step 1 prescribes it per beat.
**Fix-spec check:** Event is `_StrictModel` with documented additive discipline (models.py:383, "old snapshots round-trip") — `first_presented_day: Optional[int] = None` is round-trip safe. Stamping under campaign_lock keeps engine sole-writer; check_companion_arc precedent for a persisting sub-call is real (read in the docstring). Wire contracts: grep found NO viewer/GUI consumer of scene_context/present_events — consumers are the DM (tolerant LLM) + QA drivers, so the stub shape is not a frozen-wire break. One required addition: the scene_context docstring's "scene_context NEVER writes campaign state itself" sentence must be updated in the same PR. Standalone present_events keeps full payload (correct — it's the documented full-text escape hatch the stub points at). dup: new (re-checked: #753 is a budget-definition issue, no open issue touches event payload). P1 holds (paid on every current-era beat, generation-bound path).

### F7-4: session_recap returns "start of a new adventure" mid-campaign — CONFIRMED (P2), scope EXPANDED
**Verification:** server.py session_recap resolves ONE sid (active first) → recap_from_store → single-file read_log; a fresh session holds only the system marker → _EMPTY (recap.py:21,66-67). RE-RAN at s10: after start_session, session_recap = the empty-adventure string with 12 sessions on disk. **New evidence widening the finding:** the same re-run showed `start_session(...).previously_on` = 70 B (the _EMPTY string) because the audit's earlier probe left the prior session story-empty — i.e. **start_session's `previously_on` has the SAME single-session blind spot** (server.py:7057-7059 recaps only `session_ids[-1]`), and under lean play (fresh engine session per beat via auto-start) a story-empty prior session is the COMMON case.
**Corrected fix spec:** apply the read_log_all fallback at the shared seam (recap_from_store, or a helper used by BOTH session_recap and start_session's previously_on) rather than session_recap only: if the resolved session yields zero story beats AND other sessions exist → recap the tail of read_log_all(campaign_id, c.session_ids). Keep single-session result when it has ≥1 story beat; truly-new campaign stays _EMPTY. Apply F7-1's filter in the fallback. dup: new. P2 holds.

### F7-5: Recap is a verbatim ~12 KB concatenation with no byte budget — CONFIRMED (P2, confidence med)
**Verification:** recap.py:44-70 read directly — max_entries counts ENTRIES, no byte budget, no truncation, full-text join. Magnitude is beat-length-dependent: real campaign recap = 3,767 B (re-measured); the 11.8-12 KB figure is the synthetic fixture with ~1 KB beats (real DM prose beats are commonly that long, so plausible at depth — but not re-reproducible now that the audit probes mutated the scale fixtures' tail sessions; mechanism is source-confirmed, magnitude stays confidence-med as the auditor marked). Fix spec (max_chars budget trimming oldest-first + deterministic engine-state line from engine-mutated values only, NO LLM summarization) is invariant-clean (engine reports; DM narrates). dup: new. P2 holds.

### F7-6: Any `kind` string accepted; typo'd kind silently invisible everywhere — CONFIRMED (P3)
**Verification:** log_event (server.py:7191-7213) passes kind unvalidated; models.py:1074 `kind: str = "narration"` bare str; persist_beat `ev.get("kind","narration")`. The audit's live probe already planted a kind="narrative" row in the /tmp state copy — confirmed invisible to recap/recent_narration/recall (exact-kind filters at recap.py:18, server.py:9049, ledger.py:191 — all read). Fix-spec safety check PASSED: internal `_log_session_entry` callers use only "combat" (server.py:153) + passthrough (7212, persist_beat); start/end_session construct SessionLogEntry directly and bypass the seam — so a whitelist at _log_session_entry breaks no internal writer, and leaving the MODEL field unconstrained preserves old-log round-trip. dup: new. P3 holds.

### F7-7: Entire retrieval surface organically dead (0 recall-family calls) — CONFIRMED (P1)
**Verification:** RE-RAN both censuses on main: 277 QA transcripts → scene_context 14, recall/recall_npc/recall_decisions/session_recap/forget all 0; 68 play-state DM transcripts → scene_context 16, recall_query folds 0, recall family all 0. Numbers exact. (A naive grep counts ~450 occurrences each — those are tool LISTINGS in transcript tool inventories; the census correctly counts tool_use blocks only.) The prescription it violates is real and mandatory-voiced: SKILL.md:36 lossless rule + lib_beat_driver.sh:427 ("you MUST retrieve BEFORE you narrate… NEVER guess"). Fix spec: QA-side tally + additive engine memory_note (absent == today — invariant-clean) + planted-fact behavioral probe — all sound; keeping `forget` justified (only contradiction-repair primitive; also dead at 0 calls). dup: new (#593/#612-616 are the relations-changelog family, a different memory subsystem). P1 holds — this is the unit's central finding and the latency/quality justification for F7-1/2/8/13 being latent.

### F7-8: Ledger rebuilt from scratch after every snapshot write — CONFIRMED (P2), root-cause nuance + digest spec CORRECTED
**Verification:** store.py:119 `campaign.updated_at = time.time()` on every save → snapshot mtime/size in `_signature` (ledger.py:54-67) → full DROP+reparse backfill on next `_ensure_fresh`. log_event saves the snapshot unconditionally per streamed paragraph (server.py:7213) — re-read, confirmed. Timings accepted at the auditor's own stated confidence (med on magnitudes; probes preserved).
**Correction 1 (root cause):** "~100% of these rebuilds are false-positive staleness" is OVERSTATED — after log_event the session file also grew, so the index legitimately needs the new row; the defect there is GRANULARITY (all-or-nothing rebuild of an append-only log), while pure-state saves (HP, clock, arc progress persisted by scene_context's check_companion_arc) are the genuinely false-positive invalidations.
**Correction 2 (fix spec):** the proposed digest over `len(ch.memory)` / `len(lore)` is UNSOUND: a forget(Y)+remember(X) pair restores the same length (stale index keeps Y, misses X), and ending-overlay lore de-confliction can REPLACE lore items without changing count. Digest must hash the CONTENT of the indexed projection (the exact strings backfill reads — cheap, it's a small projection), not lengths/counts. Incremental log indexing via `ledger_meta(session_id, bytes_indexed)` + the log_event save-only-when-new-sid micro-fix are sound as written. **Exposure note:** `_ensure_fresh` runs only from the recall family (+ companion_advise, server.py:6851) — all ~0 organic calls today (F7-7) — so like F7-2 the cost is latent until retrieval adoption; P2 retained because it lands mid-beat via the prescribed scene_context(recall_query=…) fold the moment adoption begins. dup: new.

### F7-9: recall_npc / recall_decisions missing OperationalError guard — CONFIRMED, severity DOWNGRADED P2→P3
**Verification:** source-confirmed: recall() has try/except OperationalError → [] (ledger.py:121-124); recall_npc (137-142) and recall_decisions (155-161) have try/finally with NO except — error propagates out of the MCP tool. Race window real but tiny: the DROP and CREATE auto-commit individually (DDL outside the insert transaction), so the no-such-table window is sub-millisecond; during the (longer) insert transaction WAL readers see the committed post-CREATE EMPTY table and silently return [] (degraded, not thrown). Exposure today ≈ 0: no viewer/GUI consumer of either tool found by grep, and organic usage is 0 (F7-7). Real defect, 3-line fix, keep — but a P2 "wastes a whole ~100 s beat" requires a concurrent multi-process rebuild landing in a sub-ms window on a tool nothing calls. **P3.** dup: new.

### F7-10: Recall hits carry no usable temporal anchor (day=0, t=rebuild time) — CONFIRMED (P3)
**Verification:** ledger.py:180 `_ins(... day=0)` default; line 196 passes neither day nor e.t; line 185 inserts `time.time()` at backfill. SessionLogEntry has real `t` (models.py:1073) discarded by backfill; no `day` field on the model → the additive Optional[int] spec is round-trip safe. "Chronological only by accident" is accurate (insert order within one backfill pass happens to be chronological per section). npc_fact list-index-as-t is hacky but workable and avoids touching the list[str] schema. dup: new. P3 holds.

### F7-11: No log rotation; every-beat tail re-parses the whole campaign history — CONFIRMED (P3)
**Verification:** _scene_recent_narration (server.py:9025-9056) → read_log_all (store.py:347-393) opens + pydantic-validates EVERY line of EVERY session file to return the last 8 — read directly, no tail short-circuit exists. Bounded today (25 ms @ 2,518 rows per the audit probe), strictly linear, on the every-beat lean path. Fix (newest-first walk with tail stop + slack, full walk stays default) is read-only and equivalence-testable. dup: new; relates-to #17 RE-VERIFIED present in open-issues.txt ("Per-entity state files for scale"), different layer. P3 holds.

### F7-12: remember/persist_beat echo entire memory list; persist_beat bare KeyError — CONFIRMED (P3)
**Verification:** remember returns full `ch.memory` (server.py:6372); persist_beat appends `{"id","name","memory": ch.memory}` PER memories item and `mem["character_id"]` raises bare KeyError while siblings raise ValueError — all read directly in source. Fix spec sound; one shipping note: the memory_tail/memory_count return-shape change is an LLM-facing tool-result change (no code consumer found; grep qa/ asserts before landing). Soft-cap-note (engine never deletes) respects sole-writer. dup: new. P3 holds.

### F7-13: recall returns confident hits for zero-real-match queries — CONFIRMED (P3)
**Verification:** RE-RAN kind_probe2.py on main: `recall("duchess price quiet")` → Steel Watch lore + Raphael "for a price" npc_fact; strict `recall("duchess")` → []. Exact reproduction. `_safe_match` OR-of-tokens is deliberate and documented (ledger.py:82-92 — the implicit-AND fix); the defect is the discarded bm25 rank + no matched-terms + no weak-match note (ledger.py:95-96, server.py:7236). Additive-keys fix + threshold note is sound; floor calibration correctly marked med. dup: new. P3 holds.

---

## CLEAN-VERIFIED checklist (skeptic spot-audit)
Independently re-verified in source/probe: #1 (heartbeat filters present at all 3 seams — recap.py:59-62, ledger.py:192-195, server.py recent_narration; HEAD is the #749/#763 merge), #3/#4 (exec 5-25 ms + 17.0 KB lean payload re-measured), #6 (kinds filter in SQL pre-rank ledger.py:110-120; world_state header honors kinds, server.py:7240-7258), #7 (_safe_match quoted-phrase injection safety), #8 (ledger never touches campaign_lock/snapshot; WAL+busy_timeout), #13 (log_event lock + auto-start persisted). Trusted on the auditor's preserved probes (not re-run, mechanisms plausible from source): #2 (dup scan over 143 campaigns), #5 (read_log_all ordering), #9 (persist_beat batching), #10 (remember/forget semantics — partially re-read in source, consistent), #11 (scaffold leak 0.16%), #12 (no typo'd kinds in real data — corroborated by my independent 810-row census finding 0 non-marker system rows and only canonical kinds).

## Tally
unit 07: 9 confirmed, 4 corrected (F7-1 fix-spec system-kind, F7-2 P1→P2, F7-4 scope+previously_on, F7-8 root-cause nuance + content-digest spec, F7-9 P2→P3 — F7-2/F7-9 counted in the 4 as severity-class corrections; F7-4's expansion strengthens it), 0 refuted.

Post-correction severity ladder: P1 = F7-1, F7-3, F7-7 ; P2 = F7-2, F7-4, F7-5, F7-8 ; P3 = F7-6, F7-9, F7-10, F7-11, F7-12, F7-13.

Skeptic notes for synthesis:
- The unit's findings form a dependency wedge: F7-7 (adoption) is the keystone — F7-2/F7-8/F7-9/F7-13 are latent until it lands and must be sequenced with it; F7-1/F7-3 are live today on every run.
- Two auditor claims actively corrected before they could ship a regression: dropping kind="system" from the ledger (breaks SKILL.md:47's prescribed recall-feeding system notes) and the length-based ledger digest (misses forget+remember swaps and lore overlay replacement).
- Repo SHA discrepancy: tasking said f24a102; actual main HEAD and audit SHA are a245a2c. All verification done against a245a2c.

════════════════════════════════════════════════════════════════
## UNIT 08 — VERIFIED REPORT (verbatim from unit-08-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 8 — PERSISTENCE / SESSIONS / LOCKS — SKEPTIC-VERIFIED REPORT

Verified by: skeptic pass, 2026-06-10. **Re-verified by a second independent skeptic pass,
2026-06-11** (HEAD unchanged at a245a2c): the probe (`skeptic_u8_probe.py`) was RE-RUN from a
fresh state dir — all 23 assertions reproduce; every cited line re-opened (store.py 76-394,
server.py 130-155 / 7035-7065 / 7150-7175 / 7305-7348 / 7495-7505 / 8220-8230 / 9035-9135,
models.py 89-94 / 762-776, recap.py 73-79, player_server.py 52-62); `git diff f24a102..HEAD --
servers/engine/` confirms store.py untouched (only server.py/recap.py/wrapper_progress.py/tests
changed, for #749/#750 — none alter a root cause); test-coverage claims (test_slots.py has zero
session-log assertions; test_store.py never tests tolerant-load→save; test_dm_session_remint.py
is harness-only) and the organic save_slot use (qa/transcripts/ow-relaunch-3lens.md:27)
re-confirmed; viewer grep shows it only NUDGES check_consequences in prompt text, never calls
engine tools — F8-2's downgrade rationale holds. All verdicts, corrections, severities, and dup
statuses below stand as written.
Audited commit: f24a102. **Verified against HEAD a245a2c** (4 commits ahead; the intervening
#749/#763 decontamination + #750 subclass-backfill touch recap.py / server.py:4826-5120 / ~9041
but do NOT alter any finding's root cause — confirmed by diff `f24a102..HEAD -- servers/engine/`
and by re-running every probe on HEAD). store.py is byte-identical at every cited line.

Method: every finding's cited file:line re-opened on HEAD; every measured probe RE-RUN from a
fresh script (`/tmp/engine-audit/skeptic_u8_probe.py`, throwaway `WORLDOS_STATE_DIR`
`/tmp/engine-audit/skeptic-state`, engine venv, seed = the auditor's 130 KB
`camp_92be4f08cbac`). All 9 mechanisms reproduced first try:

```
F8-1_postslot_in_log:true  F8-1_orphan_swept:true  F8-1_recap_contaminated:true
F8-2_live_before_B:true    F8-2_flipped_to_A:true
F8-3_tolerant_load_ok:true F8-3_key_destroyed:true
F8-4_load_ok:true F8-4_invisible_to_list:true F8-4_active_skips_it:true F8-4_world_scoped:true
F8-5_read_log_raises:ValidationError F8-5_read_all_raises:true F8-5_recap_raises:true F8-5_concat_amplifier:true
F8-7_orphan_on_disk:true F8-7_sid_not_persisted:true F8-7_in_read_all:true F8-7_missed_by_last_sid_recap:true
F8-8_junk_dir:true F8-8_only_lock:[".lock"]
F8-9_negative_hp_persisted:true F8-9_load_clamps:true
```

Verdict: **0 refuted. 4 confirmed as filed (F8-1, F8-4, F8-5, F8-6). 5 corrected** (F8-2
severity downgrade + evidence fix; F8-3, F8-8, F8-9 fix-spec corrections; F8-7 spec
behavior-change flag). Dup statuses re-checked against /tmp/engine-audit/open-issues.txt — all
hold (#640 open; #749 fixed in a245a2c but owns only wrapper-heartbeat filtering, not these
root causes; nothing in #748–#758 owns any finding).

---

## F8-1: load_slot rolls back the snapshot but NOT the session logs — CONFIRMED, P1

- severity: **P1 (kept)** | confidence: high | effort: M
- Skeptic verification: save_slot (store.py:183-197) copies only snapshot.json; load_slot
  (200-223) writes only the snapshot back. Probe on HEAD: post-slot "incinerates" entry survives
  load_slot in read_log_all AND in recap_from_store; post-slot orphan session swept in by the
  leftover branch (store.py:382-385). The #749/#763 fix that landed after the audit filters ONLY
  exact-match wrapper-heartbeat lines (wrapper_progress.is_wrapper_progress_line) — rolled-back
  story prose passes straight through, so the contamination claim holds post-fix.
- Disproof attempts that failed: (a) no compensating caller — server.py load_slot tool
  (7158-7169) is a thin store passthrough; (b) tests/test_slots.py is snapshot-fidelity only,
  zero session-log assertions; (c) no open issue owns slots.
- Reachability check (severity): slots ARE organically used in real play — a QA transcript
  (qa/transcripts/ow-relaunch-3lens.md:27) shows the DM calling `save_slot` unprompted at
  session close. load_slot is the advertised "reload" path. Silent, PERMANENT corruption
  (two timelines interleaved in an append-only log, stable t-sort at store.py:392) of the DM's
  only lean-beat memory justifies P1 even at low load_slot frequency.
- Fix spec: AS FILED (sessions byte-length manifest at save_slot; archive-orphans +
  archive-tail-then-truncate at load_slot; manifest-less slots degrade to today). Invariant
  check PASSES: engine sole writer under the caller-held lock; additive (old slots degrade);
  archive dir `sessions/rolled-back-<ts>/` is invisible to read_log_all (glob `*.jsonl` is
  non-recursive — verified store.py:374). ONE spec addendum: a session file SHORTER than its
  manifest length (possible after an intervening restore of an older slot) must degrade to
  leave-as-is, never pad/raise — add to the test matrix.
- test: as filed + shorter-than-manifest degrade case.
- dup: new (adjacent to #749's decontamination — different root cause; #749's fix verifiably
  does not cover this).

## F8-2: check_* tools save unconditionally; any cross-campaign evaluation flips the live pointer — CORRECTED (P1 → P2)

- severity: **P2 (downgraded from P1)** | confidence: high (mechanism, re-proven) / med
  (production frequency) | effort: S-M
- Skeptic verification on HEAD: unconditional `save_campaign(c)` confirmed at server.py:7313
  (check_consequences), 7501 (check_companion_arc), 8227 (check_faction_arcs) — all reached
  with zero results; scene_context delegates to check_companion_arc at server.py:9128 every
  beat. Probe: one zero-mutation load+save of camp_A flipped active_campaign_id from camp_B to
  camp_A. **Also found: world_tick saves unconditionally too (server.py:7344)** — any fix at the
  tool level must include it; the chokepoint fix covers it for free.
- Evidence CORRECTION: the auditor's quote — scene_context's docstring "claims it NEVER writes
  campaign state itself" — is selectively framed. The docstring (server.py:9108-9111) explicitly
  acknowledges "including check_companion_arc's arc-progress save"; the NEVER-writes sentence
  scopes only the durable/recent sections. The doc is consistent; the finding stands on the
  no-op-write mechanism, not on a doc contradiction.
- Severity rationale (downgrade): P1 requires a realistic current trigger. The flap needs a
  second actor calling a check_*/scene_context on a NON-live campaign in the SAME state dir
  mid-run. Today no in-repo surface does that: the harness re-grounds only the campaign it
  resolved (self-reinforcing, no flap); concurrent QA uses ISOLATED state dirs (project
  policy); the viewer (post-#741) and dashboard are file-readers; player_server is read-only.
  The #640 cold-open twin is abandoned — nothing polls it. The harm is real but latent
  (operator/agent inspecting an old campaign mid-run; future Tier-2 sub-sessions), and it
  silently weakens the #640 resolver semantics ("live = who wrote last" degrades to "who was
  POLLED last") — a P2 that should ride along whenever #640's family is touched.
- Fix spec: AS FILED (chokepoint dirty-skip in store.save_campaign: serialize with PRE-stamp
  updated_at/engine_sha, byte-compare to disk, identical → return without write/stamp).
  Invariant check PASSES: preserves sole-writer, no schema/wire change, fixes all 92 sites
  (count re-verified: `grep -c "save_campaign(" server.py` = 92). Noted composition hazard:
  after a TOLERANT load (F8-3) the dump differs from disk → write proceeds → still destroys
  unknown keys, so F8-3's backup must land independently. load_slot's byte-identical-restore
  edge (skip = no fresh stamp) is harmless. The weaker tool-level alternative must add
  world_tick to the three check_* sites and keep betrayal_warning save-free.
- test: as filed.
- dup: enriches #640 (open, G3 #1 blocker).

## F8-3: tolerant-load drops unknown keys; next save destroys them permanently — CONFIRMED, spec CORRECTED

- severity: P2 | confidence: high | effort: S
- Skeptic verification on HEAD: store.py:142-170 drops + warns (log-only, invisible at the
  table); save_campaign:128 rewrites current-schema-only. Probe: inject
  `future_field_from_newer_engine` → load OK → save → key gone from disk. tests/test_store.py
  pins load success only, never load→save.
- Fix spec CORRECTION: the filed spec self-contradicts — "copy to
  `snapshot.pre-tolerant.<ts>.json` if absent" is never absent when the name embeds a fresh
  timestamp (→ unbounded backups, one per tolerant load). Corrected shape: **fixed filename
  `campaigns/<id>/snapshot.pre-tolerant.json`, write-once** (skip if it exists — first-skew
  bytes are the valuable ones), `_atomic_write`, degrade-not-abort on backup failure; plus
  surface the dropped key names to the resume path. Confirmed nothing globs `campaigns/<id>/*.json`
  (list_slots globs `slots/*.json`; listings read `snapshot.json` by name), so the sibling file
  is inert. The do-NOT-round-trip-through-the-strict-model guidance is correct
  (`_StrictModel` is `extra="forbid"`, models.py:90-94).
- test: as filed (with fixed-name backup; second cycle creates no second backup).
- dup: new (completes #165; no open issue owns it).

## F8-4: enumerators use the STRICT parse only — tolerant-loadable campaign invisible to resolver/listings — CONFIRMED

- severity: P2 | confidence: high | effort: S
- Skeptic verification on HEAD: bare `Campaign.model_validate_json` + `except Exception:
  continue` confirmed at store.py:253 (list_campaigns), 272 (campaigns_for_world), 313-315
  (active_campaign_id); player_server.py:58-62 sits atop list_campaigns. Probe: unknown-key
  snapshot → load_campaign True; list_campaigns excludes it; active_campaign_id resolves the
  OTHER campaign; campaigns_for_world empty. Two divergent definitions of "loadable" with the
  #640-authoritative resolver on the narrower one — exactly as filed.
- Fix spec: AS FILED (shared `_load_summary` using the SAME factored tolerant helper as
  load_campaign; tolerant retry only on strict failure; optional player_server routing through
  store.active_campaign_id). Invariant check PASSES (read-only, no schema change).
- test: as filed.
- dup: enriches #640.

## F8-5: one torn session-log line poisons read_log/read_log_all until hand-repair — CONFIRMED

- severity: P2 | confidence: high | effort: S
- Skeptic verification on HEAD: read_log (store.py:335-344) per-line `model_validate_json`
  with no tolerance; append_log (328-332) blind append. Probe: torn final line →
  read_log raises ValidationError, read_log_all raises, recap_from_store raises (recap.py:77 →
  read_log); start_session's recap read (server.py:7057-7059) and
  `_scene_recent_narration` → read_log_all (server.py:~9044) sit on those exact paths.
  Amplifier re-proven: next good append concatenates onto the unterminated line (one line
  containing both) — poison grows. Torn lines are plausible on this fleet: appends are
  unfsynced + buffered, and the 16 GB Mac has a documented OOM-kill history.
- Fix spec: AS FILED (reader-side per-line skip + warn-once-with-count; append-side
  newline-prefix when last byte != `\n`). Invariant check PASSES: read path stays read-only;
  the stat+1-byte check in append_log runs under the caller's campaign_lock so no writer race.
  Noted explicitly: skip-bad-lines silently drops the torn entry forever — acceptable
  (one entry) vs a bricked resume.
- test: as filed.
- dup: new.

## F8-6: snapshot + session log have no shared transaction — ghost beats — CONFIRMED

- severity: P3 | confidence: med | effort: S
- Skeptic verification: _log_session_entry (server.py:137-148) appends to disk immediately via
  _ensure_session + append_log, mid-tool, before the tool's save — confirmed. scene_context's
  durable/recent reads are lock-free (`_require` + read_log_all, server.py:9116-9132) —
  confirmed. Evidence is code reading (no probe); med confidence is honest. Accept-and-bound
  spec (log-last convention + AST lint + prose-only consumer doc) is proportionate to a P3 and
  violates no invariant; the flush-variant correctly gated behind F8-2's chokepoint.
- dup: new.

## F8-7: engine-side session remint — orphan session file + repeated numbering — CONFIRMED, spec flagged

- severity: P3 | confidence: high | effort: S
- Skeptic verification on HEAD: _ensure_session (server.py:7040-7046) mutates in-memory;
  append_log writes immediately; a raise before the tool's save strands the file. Probe:
  orphan on disk True; sid not in reloaded session_ids True; orphan beat IS in read_log_all
  (leftover sweep) but MISSED by start_session's recap, which reads only `session_ids[-1]`
  (server.py:7057-7059). Dup check re-verified on HEAD: tests/test_dm_session_remint.py is
  entirely the HARNESS `claude -p --session-id` remint (bash helpers in qa/lib_beat_driver.sh)
  — does not cover this engine-exception class.
- Fix spec: kept, with a flag the auditor omitted: switching start_session's recap from
  single-session `recap_from_store(prior)` to a campaign-wide `read_log_all` tail is a
  deliberate BEHAVIOR CHANGE for normal multi-session play too (a short prior session's recap
  now reaches back into earlier sessions). That is arguably better and recap text is prose
  (not a frozen wire contract), but the red-first test should pin the new semantics
  explicitly. Option (2) save-on-mint stays the conservative alternative.
- test: as filed + pin the recap-spans-sessions semantics.
- dup: new.

## F8-8: campaign_lock on a typo'd id mints a junk dir — CONFIRMED, spec CORRECTED

- severity: P3 | confidence: high | effort: S
- Skeptic verification on HEAD: store.py:98-99 `mkdir(parents=True)` before any existence
  check. Probe: lock on `camp_typo_skeptic` → dir exists containing only `.lock`.
  safe_path_segment (76-85) bounds it to litter — confirmed.
- Fix spec CORRECTION: drop the filed alternative "prune lock-only dirs in list_campaigns" —
  it turns a pure read into a writer/deleter, contradicting the unit's own clean-verified #5
  (pure reads don't write) and adding a delete race against a concurrent create_campaign.
  Corrected spec: the primary option only — locks at `state/locks/<id>.lock` so campaign dirs
  are created solely by save_campaign. (Lock files are transient; no migration concern —
  but land it atomically with nothing else holding old-path locks, i.e. a normal deploy.)
- test: as filed.
- dup: new.

## F8-9: no re-validation at save time — CONFIRMED, spec CORRECTED

- severity: P3 (guard gap, no live bug) | confidence: high | effort: S
- Skeptic verification on HEAD: `_StrictModel` config is `extra="forbid"` only — no
  `validate_assignment` (models.py:90-94); `_clamp_vitals` is a model validator (models.py:763)
  so it runs on construction/validation only; save_campaign dumps without validating
  (store.py:128). Probe: in-memory `current_hp=-5` persists to disk raw; reload clamps —
  i.e. invalid values are live for every direct-JSON reader (viewer/gates) until the next
  load, exactly as filed.
- Fix spec CORRECTION (two-part): (a) save_campaign must dump the **validated** model
  (`validated = Campaign.model_validate(campaign.model_dump()); _atomic_write(...,
  validated.model_dump_json(...))`) — the filed spec validates but doesn't say to persist the
  validated (clamped) copy, which is the entire point; (b) note the failure-mode change: a
  genuinely invalid aggregate now RAISES at save (aborting the mutation) instead of persisting
  garbage — correct, but the red-first test should pin it deliberately. Composes with F8-2's
  chokepoint (validate, then byte-compare the validated dump).
- test: as filed + raise-on-invalid case.
- dup: new.

---

## CLEAN-VERIFIED (skeptic spot-checks)

Auditor's 14 clean items accepted. Independently re-verified on HEAD: #2 (start_character's
pre-lock `c0` is discarded; re-`_require`d inside the lock — server.py:1523/1547/1586),
#4 (_atomic_write temp+flush+fsync+replace, store.py:108-115), #6 (strict `>` + sorted iterdir
tie-break, store.py:309-325), #10 (test_slots.py: verbatim copy, foreign/corrupt/missing
refusal, traversal rejection), #11 (safe_path_segment on campaign ids AND slot names before
any I/O), #12 (player_server read-only over store reads), and the 92-call-site count. Items
#1 (85/88 lock scan), #5, #7-#9, #13, #14 rest on the auditor's systematic scans/measurements;
mechanisms spot-checked where cheap (get_state save-free; end_session no-active early-return at
server.py:7082-7083), no contradiction found.

## Tally

unit 08: **4 confirmed, 5 corrected, 0 refuted.**
Corrections: F8-2 severity P1→P2 + evidence reframe (+world_tick site); F8-3 backup naming
(fixed-name write-once); F8-7 recap behavior-change flag; F8-8 drop prune-in-list alternative;
F8-9 persist-the-validated-dump + raise-on-invalid flag.

════════════════════════════════════════════════════════════════
## UNIT 09 — VERIFIED REPORT (verbatim from unit-09-verified.md)
════════════════════════════════════════════════════════════════

# Unit 09 (economy) — SKEPTIC-VERIFIED report
Repo: /Users/lume/ClawDnD-val. Audit baseline f24a102; verified against HEAD a245a2c (f24a102 is an ancestor; `git diff f24a102..HEAD` touches only level-up/subclass-backfill + #749 narration-heartbeat code in server.py — ZERO economy-surface changes, so all citations hold on main RIGHT NOW; server.py line numbers cited below are f24a102's, +~31 on HEAD past line 4827).

Verification method: every cited file opened on HEAD; every "Measured" claim re-run live (uv run python against servers/engine); the F9-4 census re-run from scratch with the correct tool-name pattern (`"name":"mcp__clawdnd-engine__<tool>"`); dup-status re-checked against /tmp/engine-audit/open-issues.txt (146 issues, incl. #748–#758); fix-specs checked against engine invariants (sole-writer, additive/_StrictModel round-trip, engine-rolls, frozen wire contracts).

**Tally: 11 confirmed, 2 corrected (F09-8 severity downgrade, F09-11 scope expansion to gain()), 0 refuted.**
Zero refutations is unusual for this audit series, so the disproof attempts are documented per finding — every measured claim reproduced bit-for-bit, and three genuine disproof attempts (update_character compensation, in-flight issue ownership, existing-test coverage) all failed to refute.

---

## F09-1 — itemcatalog.resolve() crashes (AttributeError) on every unique-substring match — CONFIRMED [P1|high|S]
- Re-ran live: `ic.resolve('bag of hold')` → `AttributeError: 'dict' object has no attribute 'lower'`; `ic.find('bag of hold')` → exactly `['Bag of Holding']`. Reproduced on HEAD.
- Code: itemcatalog.py:263-265 `matches = find(name, limit=2); if len(matches)==1: return idx.get(matches[0].lower())`; find() returns list[dict] (itemcatalog.py:269-277). Dead-on.
- Disproof attempts: (a) compensating caller — NO: resolve() is called by `_apply_item_catalog` (server.py:5806) and `lookup_item` (5827); nothing catches AttributeError on those paths (the `_index()` H1 try/except is flatten-time only), so the exception propagates to a raw MCP tool error. (b) existing test — confirmed `test_resolve_loose_substring_when_unambiguous` (tests/test_itemcatalog.py:98-100) passes `"  bag of holding  "` which strips to the EXACT index key; the substring branch is genuinely dead under test. (c) in-flight issue — none of #748-758 or the 146 open issues touches itemcatalog.
- Fix-spec invariant check: `return matches[0]` is the same live lru-cached dict the exact path returns — identical exposure, no new hazard (clean-verified #11 copy-caveat still applies to future enrichers). PASS.
- Severity: P1 correct (not P0 — zero call volume today per F09-4 means no gate breaks TODAY; it's the first crash once adoption lands). dup: new (re-checked).

## F09-2 — buy_item charges per-unit price ONCE regardless of quantity — CONFIRMED [P1|high|S]
- Re-ran (exact buy_item body simulation, models+inventory): 100 gp purse, Potion of Healing (catalog 50.0) qty 5 → purse {gp:50}, qty 5 granted. Should have been an insufficient-funds raise at 250 gp.
- Code: server.py:5941-5942 `inventory.pay(ch, cost_gp)` once, then `inventory.add_item(ch, name, quantity, …)`. sell_item mirror confirmed at 5953-5954 (gain(price_gp) once for a qty stack).
- Disproof attempts: no test buys quantity>1 (verified — tests/test_itemcatalog.py buy tests all qty default); no caller compensates (pay() is called from exactly one site, grep-verified). No issue owns it.
- Fix-spec invariant check: `total = cost_gp * quantity` + additive response fields {unit_cost_gp, total_cost_gp} — additive wire, no model change. Failure-ordering safe: pay-then-add with no save until end (clean-verified #8), so a qty<=0 add_item raise never persists the debit. The `_gp_to_cp(cost_gp)*quantity` refinement for sub-cp exactness is sound. PASS.
- Correction (minor): depends_on changed F9-13 → none (F09-13 supplies the regression matrix; the fix doesn't depend on it). dup: new.

## F09-3 — 760/960 catalog items (all magic items) purchasable for 0 gp — CONFIRMED [P1|high|M]
- Re-ran: index 960 total, 760 with falsy cost; kind split matches exactly (weapon 445, wondrous 138, armor 104, ring 21, potion 20, wand 14, staff 12, rod 6). Bag of Holding cost 0.0 vs Candle 0.01 — indistinguishable in kind. Free-buy reproduced: 50 gp purse + BoH catalog buy → purse unchanged, bag granted.
- Code: itemcatalog.py:100-108 `_num(value, default=0.0)` maps None/"" → 0.0; server.py:5932-5935 sentinel `rec.get("cost", 0.0)` — key always exists → pay(0.0) succeeds.
- Strengthening fact found during verification: `rec["cost"]` has exactly ONE consumer in the engine (server.py:5933, grep-verified) — the `cost: None` flatten change is fully contained; only the buy_item sentinel needs the explicit None→ValueError branch the spec already specifies.
- Fix-spec invariant check: catalog dict is module-local, never persisted (no _StrictModel round-trip exposure); `cost: null` in lookup_item/find_items responses is an LLM-read payload, not a frozen viewer/move wire contract. PASS.
- Severity P1 correct (same not-P0 logic as F09-1). dup: new.

## F09-4 — Entire economy/item toolchain DARK in real play: 0 calls — CONFIRMED (census re-run exactly) [P1|high|M]
- Re-ran the census from scratch over qa/transcripts/*.jsonl + play-state/**/*.jsonl with pattern `"name":"mcp__clawdnd-engine__<tool>"`: all 12 economy tools (buy/sell/adjust_currency/add/remove/equip/attune/lookup/find_items/encumbrance_status/use_item/downtime) = **0**; controls match the auditor's histogram EXACTLY: award_xp 32, attack 59, skill_check 235, update_character 32. (Note: a naive unprefixed-name grep returns 0 for controls too — the auditor used the correct prefixed pattern; verified.)
- NEW disproof attempt (the auditor missed this one, and it could have refuted the finding): `update_character` CAN patch list fields including inventory (server.py:2552+ docstring). Checked all 32 update_character calls in the corpus for patches touching "inventory" or "currency": **0**. No compensating persistence path exists. Finding is STRONGER than filed.
- Prompt-side root confirmed: grep of skills/dungeon-master/ → only economy-tool mention is `adjust_currency` in reference/death-and-reroll.md:81; SKILL.md:15 states the generic state-through-engine principle; the bolded XP non-negotiable (which demonstrably drives 32 award_xp calls) exists in the persist_beat section — no economy equivalent.
- Fix-spec check: prompt + qa/assert_behavioral.py (exists, verified) check — no engine/wire change. PASS. dup: new — enriches #602/#604 (their upstream data source); #620/#594 are player-verb move-kinds, not DM adoption — not dups (re-checked).

## F09-5 — equip_item is 100% cosmetic (AC/attack never change) — CONFIRMED [P1|high(broken)/med(stage-2)|L, stage-1 S]
- Code-verified on HEAD: `Item.equipped` (models.py Item class — 7 fields total, confirmed) consumed ONLY by inventory.py + the equip_item tool (server.py:5882-5889, returns bare `it.model_dump()`) + starting-gear seeds (server.py:1274-1275); grep-verified. `Character.armor_class` is a static int written at creation/update_character only. `_combat_numbers` read in full: prof + STR/DEX mods only — never inspects inventory ("attack trusts the bonus you hand it" per its own docstring). No slot rules anywhere.
- Disproof attempts: #272 (compare-on-hover) and #605 (attunement counter) re-read in open-issues — both viewer display; nothing open owns engine AC application. No test asserts AC changes on equip (suite read).
- Fix-spec invariant check: Stage 1 (advisory response fields) — additive, TELL-not-enforce pattern, safe. Stage 2 (engine writes armor_class on catalog-resolvable equips) — sole-writer respected (engine mutation under lock), no new persisted fields, old snapshots round-trip. FLAG kept at med confidence: stage 2 creates a two-writer tension with DM `update_character(armor_class=…)` (effects like Mage Armor flow through that path today); the spec's own staging (ship stage 1 first, stage 2 as designed work) is the right invariant-safe shape. PASS with flag.
- Severity P1 correct as a package with F09-4 (the archetypal mech failure the moment adoption lands); not P0 today (dark toolchain). dup: new.

## F09-6 — Catalog flatten drops armor dex-mod rules; Shield misrepresented as "AC 2" — CONFIRMED [P2|high|S]
- Re-ran: `resolve('Breastplate')` → {ac:14, properties:[]} (no dex rule); `resolve('Shield')` → {kind:armor, ac:2, properties:[]}; Plate 18. Code: itemcatalog.py:171-188 armor branch keeps only `_int(fields.get("ac_base"))`; the MagicItem FK branch (215-220) likewise. `_catalog_describe` (server.py:5784-5785) bakes "AC 2" into granted-item descriptions.
- Fix-spec invariant check: additive record keys on a non-persisted dict; describe-string change is LLM-payload only. PASS. dup: new.

## F09-7 — #756 determination: NOT viewer-only; owned items persist zero structured stats — CONFIRMED [P2|high|M]
- Verified both halves: catalog side healthy (`resolve('Longsword')` → damage 1d8, slashing, cost 15.0 — live); owned-item side bare (Item model = exactly {name, quantity, weight, equipped, requires_attunement, attuned, description}; `_apply_item_catalog` (server.py:5794-5816) holds the full `rec` and returns only name/weight/attune/describe-string — structure discarded at grant time, confirmed at add_item:5857 which drops rec via `_`).
- Fix-spec invariant check: additive Item fields with defaults — old snapshots round-trip under _StrictModel (declared fields, defaults fill); the copy-don't-alias warning re the lru-cached rec is load-bearing and correct (resolve() returns live index references — verified). Stacking note: add_item's identity check (inventory.py:126-134) doesn't compare the new fields, but identical catalog grants produce identical values — spec's "assert it" is the right guard. PASS.
- dup: enriches #756 (re-read in open-issues: filed as inspector/viewer gap; the engine-side root cause is this finding's contribution). Confirmed.

## F09-8 — Encumbrance surfaced-only, never called, no rule effect — CORRECTED: severity P2 → P3 [P3|high|M]
- Facts all confirmed: inventory.encumbrance (inventory.py:78-95) referenced once (the tool, server.py:5960-5964); grep over servers/engine finds no consumer in combat/travel/worldsim/sheet; census 0 calls; arithmetic correct (code-read: >5×/10×STR variant, 15× capacity, coins/50).
- CORRECTION — severity inflation: the 5×/10× thresholds are the SRD *variant* (optional) rule; with zero observed demand in 345+ transcripts, a story-first north star, and a TELL-only fix, this is polish behind F09-4 adoption, not a P2 correctness item. (The one core-rule violation — nothing stops carrying >15×STR — has no gate hanging on it either.) Downgraded to P3; fix-spec (derived-on-read additive block in get_character/party brief, enforcement deferred to combat/travel unit) is invariant-clean and kept as-is.

## F09-9 — sell_item: no catalog awareness, no price sanity; sell-above-list unbounded — CONFIRMED [P2|high|S]
- Re-ran the mechanism (gain unbounded, no catalog join): sell path is remove + gain(price_gp) + save (server.py:5948-5956, code-read on HEAD); `_catalog_describe` carries no cost tag (verified) so the DM lacks list price in-context at the moment of sale. Catalog cost of Potion of Healing 50.0 (live).
- Fix-spec invariant check: additive response fields (catalog_cost_gp, warning) + optional gated seed-param cap defaulting off — TELL pattern, additive wire. PASS. dup: new; complements #755 (re-read: UI concat bug — no overlap). Confirmed.

## F09-10 — adjust_currency can't make change; no value-spend tool path — CONFIRMED [P2|high|S]
- Re-ran: {gp:4, sp:12} + `adjust_currency(gp=-5)` → ValueError "a coin denomination would go negative" (raw, no purse/suggestion — server.py:5904-5913 passes it through); `inventory.pay(ch,5)` on the same purse → {sp:2} (change-making works and is tested — test_pay_makes_change exists, test_inventory.py:21). Grep-verified: pay() reachable ONLY via buy_item (server.py:5941), gain() only via sell_item (5954) — no value-based spend/earn tool exists.
- Fix-spec invariant check: additive optional params spend_gp/earn_gp on the same tool + enriched error message — additive wire, Decimal-exact path already exists. PASS. dup: new.

## F09-11 — purse canonicalization destroys pp/ep — CORRECTED: scope is pay() AND gain() [P3|high|S]
- Re-ran pay: {pp:10} pay 0.01 gp → {gp:99, sp:9, cp:9, pp:0} — confirmed as filed.
- CORRECTION (scope too narrow): `gain()` has the identical bug — measured: {pp:10} + `gain(1)` → {gp:101, pp:0}. Root is shared: both rebuild the WHOLE purse via `_from_copper` (inventory.py:29-33, gp/sp/cp only; pay:45, gain:52). So every sell_item (the only gain caller) also vaporizes a noble's platinum.
- Corrected fix-spec: pay() → greedy change spending smallest denominations first, breaking only the minimal higher coin, untouched denominations preserved; gain() → add the earned value as gp/sp/cp increments WITHOUT rebuilding the purse (never canonicalize unspent coins). gp/sp/cp-only purses must behave byte-identically (regression-test). Value-conservation property covers both. Pure inventory.py change. PASS.
- Severity stays P3 (value-exact, fiction-wrong). dup: new.

## F09-12 — No plot flags; equipped & attuned items sell/remove silently; false re-attune error — CONFIRMED [P3|high(behavior)/med(priority)|M]
- Re-ran both behaviors: equipped+attuned ring removed silently via remove_item (predicate is preference, falls through to matches[0] — inventory.py:98-106); re-attune at the 3-limit → ValueError "already attuned to 3 items" (false — inventory.py:167-175 fallback + count check). Item model has no provenance fields (7 fields, confirmed).
- Disproof attempt: test_remove_prefers_unequipped (test_inventory.py:145) confirms the preference is intentional for stacks — but the silent-removal-of-the-only-copy case has no test and no signal, as filed.
- Fix-spec invariant check: additive `plot`/`stolen` Item fields with False defaults (round-trip clean, same patch train as F09-7), TELL-not-block for equipped/attuned consumption, no-op re-attune — all invariant-clean. PASS. dup: new.

## F09-13 — Economy stress structurally untested; table-driven suite — CONFIRMED [P2|high|M]
- Suite counts verified exactly: test_inventory.py 20 tests, test_itemcatalog.py 27 tests, all single-path; the lookalike-input fuzzy test (F09-1) and the qty=1-only buy tests (F09-2) confirmed first-hand. F9-1/2/3 all ship green under the current suite — the antipattern claim (A3/A8) holds.
- Fix-spec check: table-driven matrix + purse-conservation property + xfail rows tagged to finding IDs; single-process pytest per repo policy — matches the allowlist rules. PASS. Note: the matrix must now include the gain()-side denomination row from corrected F09-11. dup: new.

---

## CLEAN-VERIFIED (skeptic re-checked; two annotations)
Items 1-3, 5-10, 12 re-confirmed by code-read and/or live run as part of the finding verifications above. Annotations:
- #1 (copper math value-exact): holds for VALUE; denomination preservation explicitly does NOT hold for gain() either — folded into corrected F09-11 (was implicitly pay-only).
- #4 (`_split_one` copies every Item field): overstated — the clone drops `equipped`/`attuned` (defaults False, inventory.py:115-118). Functionally correct for its call sites (splitting an unequipped/non-attuned unit off a stack), and will silently drop F09-7's NEW Item fields if not updated — F09-7's implementer must extend _split_one. Upgraded from "clean" to "watch item on the F09-7 patch train".
- #8 (no partial-state persistence): re-confirmed on HEAD — `_require` fresh-loads per call, save-at-end-only under campaign_lock; pay-then-add exception ordering never persists (relevant to F09-2's fix shape).
- #11 (live lru-cache references): re-confirmed; resolve()/find() return live index dicts; F09-1's one-line fix keeps the same exposure (no regression), F09-7 must copy.
- #12 (weight 0.0 falsy quirk, server.py:5812): re-confirmed, still judged not-filed correctly.

## Census provenance (F09-4, replayable)
`find qa/transcripts play-state -name "*.jsonl" | xargs grep -ho '"name":"mcp__clawdnd-engine__<tool>"' | wc -l` → buy_item 0, sell_item 0, adjust_currency 0, add_item 0, remove_item 0, equip_item 0, attune_item 0, lookup_item 0, find_items 0, encumbrance_status 0, use_item 0, downtime 0; award_xp 32, attack 59, skill_check 235, update_character 32. update_character patches touching inventory/currency: 0.

════════════════════════════════════════════════════════════════
## UNIT 10 — VERIFIED REPORT (verbatim from unit-10-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 10 — VERIFIED (skeptic pass)

Repo: /Users/lume/ClawDnD-val @ a245a2c (main lineage of f24a102), read-only.
Skeptic protocol: every cited file:line re-opened on main; measurements re-run where cheap
(lore FTS repro, __notoc__ census, snapshot scans, session-log speaker split, roster size,
transcript greps); fix-specs checked against engine invariants (sole-writer, additive
round-trip, engine-rolls, frozen wire contracts); dup_status re-checked against
/tmp/engine-audit/open-issues.txt.

**Tally: 7 confirmed, 4 corrected, 0 refuted.**

---

## F10-1 [P1 | high | M] lookup_lore buries the dedicated lore page for natural queries — CORRECTED (evidence + fix-spec)

**Verified mechanism (re-run on main):**
- `lorebook._safe_match` (lorebook.py:90-93) tokenizes `[A-Za-z0-9]+` → `"Wyrm" OR "s" OR "Crossing"` — re-ran, exact.
- `lookup_lore("baldurs-gate", "Wyrm's Crossing")` → 5 authored pages, `wyrm-s-crossing.md` absent. **Repro re-run, exact match to audit.** `"Wyrms Crossing bridge"` → wyrm-s-crossing.md first. Corpus: 356 pages, 5 tier-0.
- Tier-0 absolute precedence (`_match_tier(0,fetch)+_match_tier(1,fetch)` then `[:cap]`, lorebook.py:204-221) — confirmed.

**Corrections:**
1. **Evidence overclaim struck:** "Elfsong Tavern" phrased naturally **works today** — re-ran: returns `elfsong-tavern.md` at #2 ("Elfsong"/"Tavern" produce no 1-char tokens and only 1 authored page matches). The failing classes are (a) possessive queries and (b) stopword-heavy queries ("the Counting House" — re-verified failing).
2. **Fix-spec correction (design-intent):** tier-0 absolute precedence is **deliberate**, not accidental — the `_match_tier` comment explains it exists so short authored pages aren't bm25-buried by the 351-page wiki tier (the post-canon de-confliction guard). A naive rank-interleave can regress that property. Also, **measured: dropping 1-char tokens alone is insufficient** — for "the Counting House", all 5 authored pages still match on `"the"`, three at rank ≈ −0.0 (noise), and still consume the cap. Corrected spec:
   - (a) `_safe_match`: drop tokens with `len(t) < 2` unless that empties the match (fixes the possessive class; verified empirically it surfaces wyrm-s-crossing.md at #3).
   - (b) tier-0 **noise-floor, not interleave**: fetch tier-0 with `rank`; keep tier-0-first ONLY for authored pages whose |rank| clears a noise floor (e.g. ≥ a small absolute epsilon, or within a bounded window of the best tier-1 |rank|). Verified empirically: a noise-floor on the −0.0 matches surfaces `counting-house-baldur-s-gate.md` at #3 while `baldurs-gate.md`/`factions.md` (genuine matches) stay on top. Frame the PR as *preserving* the authored-canon-surfaces guarantee, tightening it to genuinely-matching authored pages.
   - (c) supersedes/gutted/canon_header untouched; tier-1-empty corpora (sundered-reach) byte-identical — both still required.
- severity P1 stands (docstring's own example fails; a whole query class of the 351-page corpus unreachable; #606's Lore tab reads the same retrieval) — not P0, no gate breaks today.
- test: red-first — tmp corpus (5 weak authored + 1 exact-title wiki) top-5 on possessive query AND on a stopword-heavy query; authored wins genuine tie; real-corpus "Wyrm's Crossing" → wyrm-s-crossing.md; byte-identical when no 1-char/stopword-noise involved.
- dup: new (#606 = viewer Lore tab over same retrieval — not a dup). effort M, confidence high.

## F10-2 [P2 (downgraded from P1) | high | M] generate_parley_options: no NPC binding, attitude-blind DC, dead `situation` param — CORRECTED (severity + framing)

**Verified:** server.py:6636-6731 re-read on main — no `npc_id` param; body reads only actor/difficulty/house_rules/event_id; `_suggested_dc` (:6627-6632) is a function of two strings; `situation: str = ""` in the signature with **zero body references** (confirmed dead). Transcript evidence re-run: 48 calls carrying ≥20-char situation prose, 8 distinct shapes, all rich. Attitude data load-bearing elsewhere (endings/*.json gates) — confirmed.

**Correction — severity P1 → P2:** the tool's documented contract is explicitly actor-centric ("lays out the PLAYER'S options… suggested_dc comes from a fixed band keyed off `difficulty`"). DM-supplied difficulty is the *documented* design, and the DM has the target's attitude_value in scene_context every beat — so this is a design gap / ergonomics enhancement plus one genuine small defect (the dead `situation` schema lie burning DM output tokens), not a broken contract. No gate breaks; nothing errors. P2.

Fix-spec verified invariant-safe: additive `npc_id=""` + aliases; npc block {id,name,attitude band,attitude_value,met}; attitude-derived default difficulty as a band shift (explicit non-default difficulty wins); unknown id degrades like event_id; echo `situation`; read-only stays read-only; npc_id absent → byte-identical payload. Engine-rolls invariant unaffected (tool still never rolls).
- test: as filed (hostile/−60 default → hard band; explicit override; absent → today's payload; npc.id+met carried).
- dup: enriches #751 (viewer parley focus — engine anchor) and #319 (difficulty context label); attitude-blind-DC + dead-situation core is new. effort M, confidence high.

## F10-3 [P1 | high | S] list_canon_characters blows the MCP output cap — CONFIRMED

- Re-verified: content.list_canon_characters (content.py:155-196) has no limit; server tool (server.py:2275-2288) returns all verbatim. Re-measured: **n=2,076, 159,828 bytes compact JSON (~40K tokens), 0.13s** (audit's 180,630 incl. wrapper — same magnitude, both ≫ cap). Transcript evidence re-run: **249** transcripts call the tool; **8** "exceeds maximum allowed tokens" error rows naming it — exact match to audit.
- Additivity hardened: **baldurs-gate is the ONLY world shipping a character roster** (all other worlds: 0 files) — so the capped default regresses nothing anywhere; the previous full dump on the only affected world was already an error.
- Fix-spec (limit=100 + name_contains + {total, returned} mirroring roster_surface) is additive, frozen-wire-safe. Test as filed.
- dup: new (#316 = viewer Roster screen). severity P1 stands — observed erroring in real runs, flagship world.

## F10-4 [P2 | high | S] seed_world writes roster NPC ROLE into Character.attitude — CONFIRMED

- Re-verified: content.py:1758 `attitude=npc.get("role", "")` — exact, on main. world.json roster re-checked: 10 entries, **0 have `attitude`, 10 have `role`** (Jaheira: "High Harper, veteran of a hundred years"). Snapshot scan re-run: **152 snapshots, 1,520 characters with prose in `attitude`** — exact match. Destruction path confirmed: server.py:6538 shift_attitude overwrites with a track word on first influence; Character has no `role` field.
- Fix-spec verified workable: `Character.notes` exists (models.py:713, free text) and `arc_role` exists (models.py:748); seed_world doesn't currently set notes (load_canon_character uses notes for voice_hint — different path, no conflict). No model change; old snapshots round-trip (additive/_StrictModel safe).
- Test as filed. dup: new (#615/#612 are layers above the corrupted field). 

## F10-5 [P2 | high | S] recall_npc misses dialogue — ledger `who` is NAME for dialogue rows, ID for facts — CONFIRMED

- Re-verified on main: ledger.backfill indexes dialogue with `who=e.speaker or ""` (ledger.py:~196) and npc_fact with `who=ch.id` (ledger.py:~198-199); recall_npc filters `WHERE who = ?` exact (ledger.py:130-143). Engine log sites pass names: server.py:3293 (mover.name), :4277 (attacker.name), :4321/:4349 (target.name) — all confirmed. models.py:1077 `speaker: Optional[str] # character id or name` confirms the ambiguity at the model boundary.
- Evidence re-run on root play-state sessions: **71 speaker-tagged rows → 1 id-matched, 40 name-matched, 30 free-text** (audit said 73/1/41/31 — same split within scan-glob noise; example: speaker "Astarion" vs roster id, camp_4cee7b241f36).
- Fix-spec invariant-checked: read-path name-OR-id join + backfill-time `ref=<id>` enrichment are **derived-index** changes (ledger rebuilt from snapshot; sole-writer invariant 1 untouched). Test as filed.
- dup: new.

## F10-6 [P2 | high(mech)/med(policy) | M] Attitude two unreconciled tracks; set_attitude(value-only) wipes label; "unfriendly" unmapped — CORRECTED (minor)

- Re-verified on main: (a) influence moves label one full band AND value +15/−10 (server.py:6538-6539); no value↔band mapping exists anywhere (npc.py read in full). Live mismatches re-scanned: **exactly 3** — ('Recruit Veln Rusk','wary',−10), ('Sergeant Marrek Vale','wary',−10), ('Arka','wary',−10) — exact match. (b) set_attitude: `attitude: str = ""` default + unconditional `ch.attitude = attitude` (server.py:6303) → value-only call wipes the label to "". Confirmed. (c) npc.py:15-22 synonym table omits "unfriendly" → normalize → "indifferent", a band better than intended. Confirmed.
- **Corrections:** (1) "SRD's own word" — 5e SRD attitudes are hostile/indifferent/friendly; "unfriendly" is the 3.5e/PF five-step diplomacy track word (which the engine's own track mirrors, swapping unfriendly→wary). Substance unchanged: a DM writing "unfriendly" lands a band high. (2) Framing: the label/value split is **partially deliberate** (adjust_attitude's docstring: "Leaves the free-text attitude track unchanged") — so the drift is a coherence defect against an *implied* contract, not a frozen one; the audit's own owner-sign-off flag on the reconciliation policy is correct and load-bearing, keep confidence med on that part. The set_attitude wipe (b) and the synonym gap (c) are unambiguous bugs, confidence high.
- Fix-spec invariant-checked: band_for_value in npc.py, value-as-source-of-truth only when the existing label is already a track word, `if attitude:` guard on set_attitude, synonyms — no schema change, old snapshots round-trip. Tests as filed (incl. the drift invariant).
- dup: new (feeds #615/#612 — neither fixes engine math). P2 stands (#615 will render the contradiction).

## F10-7 [P2 | high | S] `__notoc__` in canon character backstories, snippet-visible — CORRECTED (spec: case-insensitivity; counts re-pinned)

- Re-measured: **515 files contain `__notoc__` case-INsensitively; 516 carry some `__x__` directive in backstory; 516/516 within the 220-char snippet window.** Critical nuance the corrected spec must carry: a case-SENSITIVE grep finds only **344** — ~170 are uppercase `__NOTOC__`, so the one-shot strip script and the CI invariant MUST be case-insensitive (`re.IGNORECASE`, which `_WIKI_DIRECTIVE_PREFIX` content.py:120 already has — generalize that, anywhere-not-prefix).
- Engine path re-verified: `_death_opener` strips (content.py:120,129); `_backstory_snippet` (content.py:281-291) and load_canon_character's verbatim `backstory=rec.get("backstory","")` (server.py:~2414) do not. Confirmed.
- Fix: (1) one-shot content script stripping `(?i)(?:__[a-z]+__\s*)+` anywhere in all string fields of characters/*.json; (2) load-time belt in `_backstory_snippet` + load_canon_character prose reads. CI invariant: zero case-insensitive hits under characters/.
- dup: enriches #758 (bestiary bios — different surface/fix site). P2 kept (borderline P3, but 25% of the player-facing picker corpus + DM-context/portrait-prompt contamination).

## F10-8 [P3 | high | S] social_check dc=0 = guaranteed success that moves attitude; rejects skills skill_check accepts — CONFIRMED

- Re-verified: `dc: int = 0` default; `success = r.total >= dc` (server.py:6507) → omitted dc auto-succeeds and the influence branch still shifts label + value. skill_check's dc=0 contract = roll-only (`if dc and dc > 0:` server.py:6596-6598). Normalization mismatch confirmed: skill_check `strip().lower().replace(" ","_")` (:6584) vs social_check bare `.lower()` (:6466) against underscore-keyed SKILL_ABILITIES (models.py:47-66 — "animal_handling", "sleight_of_hand") → "Animal Handling" raises in social_check only.
- Mitigation (auditor's own, why P3): no dc-omitted social_check calls observed in transcripts. Fix-spec (success:null no-contest degrade + adopt skill_check's normalization) is additive. Tests as filed. dup: new. depends_on F10-2 only for the error-message routing text.

## F10-9 [P3 | high(mech)/med(occurrence) | S] generate_parley_options hard-fails on non-skill proficiency entries — CONFIRMED

- Re-verified: default-skills path raises `unknown skill` on first non-SKILL_ABILITIES entry (server.py:6685-6697 area — `for sk in chosen: if sk not in SKILL_ABILITIES: raise`). models.py:786 normalizes (lower/underscore) but does NOT filter. update_character's `skills` alias passes unfiltered into skill_proficiencies (server.py:~2615-2618) → "Thieves' Tools" → "thieves'_tools" poisons the sheet and every subsequent default-path parley raises. No live occurrence in snapshots (P3 correct).
- Fix (skip-filter in the `skills is None` branch; keep raise for explicit skills; optionally filter the alias) — additive. Tests as filed. dup: new.

## F10-10 [P3 | high | S] One dangling area connection; silent area-JSON skip — CONFIRMED

- Re-verified: `areas/bloomridge-market.json` connections = ["Baldur's Gate — Lower City", "the Siltwharf Steps", "the Cloistered Quarter"]; no cloistered-quarter area file exists (14 area files listed) and the string appears nowhere else in the world — dangling. travel.reachable drops unknown ids silently (travel.py:41-44 — `dest is None` → skip). load_world_areas `continue`s on JSONDecodeError/OSError with no diagnostic (content.py:684-688) — confirmed, and it IS the odd one out vs the `[content] skipping` convention.
- Fix (author/repoint + diagnostic + CI graph-integrity walker) — content + log-line only, invariant-trivial. dup: new.

## F10-11 [P3 | high | S] lookup_lore scans the corpus twice per call — CONFIRMED

- Re-verified: server.py lookup_lore return includes `"corpus_pages": lorebook.page_count(c.world_id)` — a second full `_pages()` rglob+read; `_pages` (lorebook.py:64-83) is uncached. Bonus: `lore_corpus_pages` at server.py:547 and :588 also call page_count — an lru_cache on `_pages` (keyed world_id; corpus static at runtime, bestiary.py precedent) pays off at three sites. Fix/test as filed. dup: new.

---

## CLEAN-VERIFIED (skeptic spot-checks)

Spot-re-verified directly on main this pass: #1 adjust_attitude clamp (server.py:6316-6338 read — correct), #5 social_check read-vs-influence + ephemeral-persists-nothing (full body read — correct), #6 scene_context met-gating (server.py:~8968 read — `kind=="npc" and met`), #7 lookup_lore de-confliction stack (lorebook.py full read — sentence-level redaction before excerpt, gutted-demote-never-drop, byte-identical empty path), #8 path containment (safe_path_segment in _lore_dir:42), #9/#10 area load + bidirectional wiring (content.py:1690-1707 read), #16 find_npcs tri-state (server.py:2292-2338 read — False→None over the wire). Items 2,3,4,11,12,13,14,15 accepted on the auditor's evidence (code-paths cited were consistent with everything re-read; nothing contradicts them).

1. adjust_attitude clamp math — verified clean.
2. adjust_reputation / grant_standing clamps — accepted.
3. join_faction latch + arc arming — accepted.
4. Faction-id discipline in practice (152 snapshots, 8 fac-* ids) — accepted; create-on-unknown fork latent, watch-item.
5. social_check read-never-mutates + ephemeral-target persists nothing — verified clean.
6. met/unmet gating + no engine-side player spoiler leak — verified clean (scene_context site).
7. lookup_lore de-confliction (redact-before-excerpt, demote-not-drop, supersedes coupling) — verified clean.
8. Path containment via safe_path_segment; _lore_dir public-beats-_private currently safe — verified (watch-item).
9. World/adventure load validation loud; blocks degrade-not-abort — accepted (area silence = F10-10b).
10. Area graph dedupe + bidirectional wiring; 0 dangling region edges — verified (the 1 area edge = F10-10a).
11. Lore corpus hygiene (0/351 wiki-markup; era lines authored-only) — accepted; consistent with my zero-hit artifact scans on characters/lore.
12. get_prelude ref resolution + degrade — accepted.
13. load_canon_character dead-PC gate / dup-name / fuzzy / ability precedence — accepted (verbatim-backstory copy site read).
14. is_dead_record guards — accepted (regex read at content.py:110-129).
15. bestiary.py data side (precedence, slug parity, lru_cache) — accepted; gaps owned by #756/#758.
16. find_npcs filter semantics — verified clean.

## VERDICT (skeptic)

The auditor's unit holds up unusually well: every mechanism cited was re-found at the exact file:line on main, and every re-run measurement reproduced (lore repro exact; 2,076/160KB roster; 249 transcripts + 8 cap-errors; 152 snapshots/1,520 prose attitudes; 3 named band mismatches; 71-row speaker split; 515/516 notoc census). Nothing was refuted. Four findings needed corrections that matter for implementation: F10-1's fix must respect the *deliberate* tier-0 design and needs a noise-floor (1-char-token drop alone demonstrably does not fix "the Counting House"; "Elfsong Tavern" was an evidence overclaim — it works today); F10-2 is a P2 design-gap, not a P1 defect (the actor-centric contract is documented; only the dead `situation` param is a true defect); F10-7's strip/CI must be case-insensitive (case-sensitive tooling sees only 344 of 515); F10-6's "SRD" attribution corrected and its by-design-split framing tightened (owner sign-off flag stands).

════════════════════════════════════════════════════════════════
## UNIT 11 — VERIFIED REPORT (verbatim from unit-11-verified.md)
════════════════════════════════════════════════════════════════

# Unit 11 (images) — SKEPTIC-VERIFIED report
Verified: 2026-06-11 against /Users/lume/ClawDnD-val @ HEAD a245a2c (read-only).
IMPORTANT provenance correction: the audit was labeled "@ f24a102" but the cited release_readiness
code (handoff_image_ok / image_render_source, lines 668–681) only exists AFTER PR #762 (commit
6a8297b, merged 2026-06-10, i.e. between f24a102 and HEAD). The auditor effectively audited the
post-#762 tree — every root cause was re-checked against HEAD a245a2c and the line cites below are
current-main line numbers. f24a102..HEAD touched qa/release_readiness.py, scripts/play.sh,
servers/engine/server.py (none of the changes invalidate any finding; #762 is the load-bearing one).

Verdict tally: 6 confirmed, 2 corrected (F11-1 fix-spec/dup re-scoped; F11-3 severity P1→P2), 0 refuted.

---

## F11-1: image_render gate structurally un-passable on the VM release lane — CONFIRMED (P0), fix-spec CORRECTED onto landed #762
severity: P0 | confidence: high | effort: M | dup: new (re-scoped: #762/#730 LANDED the mac-handoff
source the auditor's spec partially re-invents; the remaining defect is unowned by any of the 146 open issues)

Verification performed (all on HEAD):
- qa/release_readiness.py:134–152 `image_render_rate`: confirmed verbatim — no network.ndjson →
  score.json fallback; `image_404s>0` → returns `(0.0, 0, f404, str(run/"score.json"))`. A
  404-only counter with NO success denominator is converted into rate 0.0 with total=f404.
- qa/release_readiness.py:655–681 rollup: `img_runs = [p for p in persona_scores if p["image_total"]>0]`
  includes the 404-fallback personas → `total_image_denominator>0` → line 676–677
  `image_render_source = "vm-network"`, which by precedence blocks the `mac-handoff` source
  (line 678 only reachable when total denominator == 0). Confirmed.
- Empirics re-checked from /Volumes/LEXAR/Codex/worldos-qa-results-fa97b34/RRI-full.json: all 5
  personas' `image_source` is `<run>/score.json` (NOT network.ndjson), image_total 3/10/9/6/47 = 75,
  rate 0.0, gate_detail "rate=0.00%; denominator=75". So #762's own design comment (release_readiness
  :661–663, "a split VM+Mac sweep structurally has NO per-run /image denominator") is empirically
  FALSE — GUI personas always record /image 404s (the VM has no _private art and a null provider, so
  every /image 404s by construction; #762's commit message itself concedes "the VM cannot serve
  gitignored _private art"). The mac-handoff lane #762 built can therefore never engage on a real sweep.
- qa/ui_playtest_score.py:116–130: confirmed — comment calls a missing /image "graceful degradation,
  not a JS error", excludes it from console health, then `is_image_404` (:126–127) counts EVERY
  `/image?scope=` 404 into `image_404s` (:129) with no success counter and no expected/unexpected
  split. Designed silhouette 404s (viewer/server.py `_serve_image` no-art comment + final
  "descriptor exists but carries no servable image" 404) are exactly what feeds the fabricated 0.0.
- #762's tests (qa/test_release_readiness.py) lock in "VM denominators take precedence over a
  handoff" — i.e. the blocking precedence is now TESTED design, not an accident. The defect narrows
  precisely to: the score.json 404-only fallback is treated as a real VM denominator.

Corrections to the auditor's text:
1. "mislabeled source": per-persona `image_source` honestly records the score.json path; only the
   rollup `image_render_source="vm-network"` is a mislabel. Minor, but the report overstated it.
2. dup/fix re-scope: #762 already shipped the mac-handoff alternative source (probe-bit + art-root +
   same-SHA + per-gate app-status checks, release_readiness:473–493, 668–681). Do NOT add a third
   parallel "mac-probe" source. Corrected spec:
   (a) CHEAPEST unblock: stop counting the score.json 404-only fallback as a VM denominator —
       return it as an `evidence_gaps` entry ("image 404s recorded but no denominator"), so
       `total_image_denominator` reflects only real network.ndjson rows and #762's mac-handoff
       source becomes reachable. (~15 LOC + flip the "precedence" test's fixture to use real
       network rows.)
   (b) Additive `X-Image-Outcome: served|no-art|placeholder|error` header in `_serve_image`
       (status/body unchanged, viewer stays pure reader) so real network captures can compute the
       rate over UNEXPECTED failures only (no-art/placeholder 404s are designed degradation per
       ui_playtest_score's own comment).
   (c) Strengthen the handoff evidence from the vacuous probe-bit to a machine-readable
       image-evidence artifact (scene + party portraits, expectation-classed) — this is F11-2's fix
       plus an optional qa/image_probe.py emitter; it upgrades the EXISTING mac-handoff source
       rather than adding a new one.
   Invariant check: all additive; viewer never writes; no engine surface touched; wire contracts
   (gate JSON keys) extended, not repurposed. PASS.
3. Severity: P0 stands — image_render is in the release gate list (release_readiness:725, doc :44)
   and hard-fails 0.0 on the canonical 5-persona VM split sweep TODAY regardless of engine behavior.

test (red-first): 5 personas with image_404s>0 + no network.ndjson + valid handoff w/ sound image
evidence → gate PASS source mac-handoff (today: FAIL 0.0/"vm-network"); score.json-only → evidence
gap, never a fabricated denominator; real network.ndjson rows with `error` outcomes still fail.

## F11-2: `image_probe_ok` vacuously satisfiable — CONFIRMED (P1)
severity: P1 | confidence: high | effort: S | dup: new | depends_on: F11-1 (lands together)

Verification:
- viewer/server.py:5784–5787 verbatim: `return bool(scope and _latest_descriptor(str(scope)))` —
  truthy on ANY parsed descriptor. Consumed at :5855 into app-status `health.image_probe_ok` (:5903).
- imagegen.py:530 confirmed: "The null provider's placeholder is cached too" — generate() cache_writes
  the payload-less placeholder; no lane sets a provider, so every Mac-QA generate_image mints one.
- viewer/server.py `_serve_image` (6849–6921): final branch 404s a descriptor with no
  path/bytes_b64/url ("e.g. null placeholder"). Probe-true + serve-404 confirmed end to end.
- Scene-scope-only confirmed (:5785–5787) — zero portrait coverage, and portraits are the bulk of
  recorded 404s in rc1 bugs.ndjson.
- Post-#762 this bit is the load-bearing image evidence for the mac-handoff gate source
  (release_readiness:473–474 per-gate AND at :485–493) — the commit message calls it "a SEPARATE,
  stricter signal"; it is not sound.
- Skeptic nuances added: (a) on canon BG3 locations the probe may be LEGITIMATELY true via ingested
  art (`_latest_descriptor` checks `_ingested_descriptor` first, :463–466) — the vacuous case is any
  scene without ingested art (generated worlds, minted locations), which is exactly the case the
  evidence must catch; (b) today no false gate-PASS can occur because F11-1 keeps mac-handoff
  unreachable — the hole goes live the moment F11-1 is fixed, which is why they must land together.
- Fix-spec check: a servability predicate ALREADY exists inline at viewer/server.py:6352–6353
  (`servable = bool(desc.get("path") or desc.get("url") or desc.get("bytes_b64"))` + placeholder/
  degraded exclusion) — factor THAT (plus _serve_image's path-containment roots) into
  `_descriptor_servable()` used by _serve_image, the :6352 site, and the probe; probe a scope SET
  (scene + portrait-<id> per party member); additive `image_probe: {probed, servable}` detail.
  Invariants: pure-reader preserved, additive keys only. PASS.

test: cache a null placeholder for the scene scope → /app-status must report image_probe_ok:false
(passes today = the bug); matrix invariant probe verdict == (GET /image status < 400).

## F11-3: per-beat process exit silently kills fire-and-forget art — CONFIRMED mechanism, severity CORRECTED P1→P2
severity: P2 (was P1) | confidence: high (mechanism) / med (frequency) | effort: M | dup: new | depends_on: F11-4

Verification:
- imagegen.py async_generate: daemon worker confirmed (`daemon=True`, thread spawn ~:648–656);
  worker body runs generate() → provider call → cache_write only at the END.
- openclaw_image.py: DEFAULT_POLL_TIMEOUT=180.0 confirmed; `_await_new_media` poll loop (:342–364);
  inline-bytes claim at :310–318. No tasks.get over /tools/invoke (doc comment :36–46) — no way to
  resume a claim.
- scripts/play.sh: one `claude -p` per beat confirmed (:282 + comment :230 "One DM turn (claude -p,
  full plugin)"; --resume per beat per :63). Engine MCP server is a stdio child of each claude -p
  (.mcp.json) → interpreter exit kills daemon threads abruptly; `_inflight` dies with the process.
- No compensation: a later identical call cache-misses (descriptor never written) and re-POSTs (new
  spend); its pre-POST media snapshot already CONTAINS the orphaned PNG → excluded from "fresh" →
  the paid image is never claimed. Confirmed unrecoverable by existing code.
Severity correction: the provider lane is unexercised today (0 real-provider results in 76
transcripts, by the auditor's own census) and generated art is NOT what the image_render mac
evidence rides (that's ingested-art probes per #762). No gate breaks today; the harm is real spend +
lost art when the lane turns on. That is P2 by the gate definitions, not P1.
Fix-spec check: `wait=False` client path exists (openclaw_image.py "if wait:" branch); detached
stdlib resolver writing only the derived cache via the atomic writer is sole-writer-safe (imagegen's
own header: the derived image cache is "the one derived artifact a background writer may" write);
`generating` status degrades exactly like today's 404→placeholder. Additive. PASS.

test: async_generate inside a child python that exits 1s later, provider generation outliving it →
descriptor still reaches ready (fails today); young `generating` descriptor suppresses re-POST.

## F11-4: media-dir watcher cross-attribution — CONFIRMED (P2)
severity: P2 | confidence: high (mechanism) / med (frequency) | effort: M | dup: new | depends_on: F11-3

Verification: openclaw_image.py:342–364 verbatim — "my image" = any file in the SHARED media dir not
in the pre-POST snapshot with mtime ≥ since−1.0, claim = `max(fresh, key=_safe_mtime)`. Two workers
(one thread per (key,scope), imagegen `_inflight`; no cross-key serialization) both see the first
landed file as fresh → same path claimed for two scopes; any unrelated gateway image task on a
shared host is claimable. Skill mandates scene+portrait art in one beat → concurrent generations are
the normal case, not exotic. Fix-spec (module-level generation lock — file-lock once F11-3's
detached resolver exists — + oldest-unclaimed FIFO + exact `since` + claimed-path recorded in the
descriptor) is derived-cache-only, invariant-safe; burst serialization is off-turn. PASS.
test: two stub clients, shared tmp media dir, interleaved drops → distinct files claimed in drop
order (today both can return the same path).

## F11-5: cache-hit returns multi-MB `bytes_b64` verbatim into DM context — CONFIRMED (P2, latent)
severity: P2 | confidence: high | effort: S | dup: new

Verification: imagegen.py async_generate hit path confirmed (`hit = cache_read(key, scope)` →
`hit["cache_hit"]=True; hit.setdefault("status","ready"); return hit` — no field filtering);
imagegen.py:339 writes `bytes_b64` for any inline provider result; openclaw_image.py inlines files
≤ MAX_INLINE_BYTES=16MB (:316–318); servers/engine/server.py `generate_image` tool returns
`imagegen.async_generate(...)` raw. Consumer grep re-run: `bytes_b64` readers are only the viewer
(/image, /portrait-* at server.py:6352/6488/6527/6899) and imagegen internals — no tool-side
consumer needs the payload. Latent (payload-bearing hits need provider lane + exact key repeat;
0/162 today) but a single hit injects ~1–5M chars into a beat. Fix (metadata-only hit return with
`has_bytes`/`byte_len`) is additive and breaks no caller. PASS.
test: seed cache with 1MB bytes_b64 → same-key async_generate returns no bytes_b64, byte_len set
(fails today); /image still serves; copy_scope round-trip unaffected.

## F11-6: no catalog consult / no scope-level idempotency — CONFIRMED (P2)
severity: P2 | confidence: high | effort: M | dup: enriches #281 (+#384 umbrella) — both are
UI-side Img wire-up; the engine-side don't-regenerate gap is unowned.

Verification: grep of servers/engine/imagegen.py for wiki_ingest/_ingested/catalog → ZERO hits;
async_generate keys only `content_hash(kind, prompt, seed, provider)`; viewer `_latest_descriptor`
(server.py:450–468) serves ingested art FIRST → a generated canon-scope image is never displayed on
an art-bearing box (pure spend), and `_newest_json_descriptor` newest-mtime + never-repeating DM
prompts (0/162 cache hits) = a new displayed face per re-introduction for non-canon NPCs. Catalog
census and transcript evidence accepted as cited. Fix-spec check: engine READING content/_private is
allowed (read-only content access; sole-writer untouched); `force` is an additive optional tool
param (wire-compatible); miss on art-less hosts falls through to generate. PASS.
test: tmp art root with portrait_raphael/wiki_ingest.json → ready/catalog with provider stub never
invoked (fails today); servable scope + new prompt → no regen unless force; unknown scope generates.

## F11-7: background generation failures completely silent — CONFIRMED (P2)
severity: P2 (borderline P3 standalone; kept P2 as the `error`-class dependency of F11-1's evidence
fix) | confidence: high | effort: S | dup: new — NOT #757 (DM-narration fallback masking; different surface)

Verification: imagegen.py `_worker` swallows everything (try generate() / except: pass), and
generate()'s degrade path ("Do NOT cache the degraded result", ~:545–554) returns the
degraded_from/error descriptor which the worker DISCARDS — no artifact, no event, nothing under any
state images/ tree. Fix-spec check: `.error` suffix (not `.error.json`) verified safe — viewer
`_newest_json_descriptor` globs `*.json` only (server.py:428–448); sibling artifact is derived,
atomic, sole-writer-safe; retry deletes stale `.error`. Feeds F11-1(b)'s X-Image-Outcome `error`
class. PASS.
test: provider raises → <scope>/<hash>.error exists with error string (fails today); successful
retry supersedes; viewer glob ignores it.

## F11-8: generate_image docstring teaches the wrong scope convention — CONFIRMED (P3)
severity: P3 | confidence: high | effort: S | dup: new

Verification: servers/engine/server.py generate_image docstring (~:1905–1921) verbatim: "pass
`scope` (a world or campaign id) to partition the derived image cache" — vs the real viewer fetch
convention (`portrait-<character_id>` / `<location_id>`); omitted scope → `_safe_scope("")` falsy →
`_latest_descriptor` None → structurally unservable. Zero field impact today (162/162 transcript
calls used correct entity scopes — the skill wins), but a schema-following non-skill caller's art is
unfetchable. Fix (~10 LOC docstring rewrite + additive `warning` key when scope omitted) is
wire-compatible. PASS.

---

## CLEAN-VERIFIED (skeptic spot-checks)
Spot-verified on HEAD: fire-and-forget return shape + <500ms no-network fast path (async_generate
read); degraded sync results not cached (generate read); null-placeholder caching key-isolated
(provider name in content_hash); /image path-containment allowlist + validated b64 + 302-no-proxy
(_serve_image read); `_inflight` (key,scope) double-spawn guard; sole-writer (only the derived image
cache is written in this unit); test files exist at servers/engine/tests/test_imagegen.py +
test_openclaw_image.py. Items 2–3, 6–9 (provider degrade chain, typed gateway errors, portrait_prompt,
re-key chain, upload hardening, skill compliance census) accepted as cited — consistent with all code
read; not independently re-run. No clean-verified claim contradicted by anything found.

## Component verdict (upheld, sharpened)
The imagegen/openclaw-client core is well-tested and invariant-clean. The release-blocking defect is
narrower than the original framing: #762 already built the Mac-handoff image-evidence lane, but
(F11-1) the score.json 404-only fallback fabricates a VM denominator that keeps that lane permanently
unreachable on real sweeps, and (F11-2) the lane's probe is vacuously satisfiable when it does engage.
F11-3/F11-4/F11-5 are latent provider-lane integrity holes (P2) to land before any
provider-configured image push; F11-6/F11-7/F11-8 are spend/observability/doc fixes.

════════════════════════════════════════════════════════════════
## UNIT 12 — VERIFIED REPORT (verbatim from unit-12-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 12 — BEAT PIPELINE (WRAPPER LAYER) — SKEPTIC-VERIFIED

Verified against /Users/lume/ClawDnD-val @ HEAD **a245a2c** (the audit cited f24a102; HEAD has moved 3 commits —
notably **#763 = the #749 heartbeat fix**, which materially changes F12-14 and the dup wording of F12-7).
Method: every cited file:line re-opened at HEAD; measurement re-run; cheapest-disproof attempted per finding;
fix-specs checked against the engine invariants (sole-writer, additive, engine-rolls, frozen wire contracts);
dup_status re-checked against /tmp/engine-audit/open-issues.txt.

**MEASUREMENT REPRODUCED** (skeptic re-run, same method: duration_ms of last `type=="result"` per dm.<ts>.jsonl,
file-index-0-per-run = cold open, mtime ≥ 2026-05-29): cold-open n=39 p50=262 p90=308 p95=326 max=370 (72% >200s);
routine n=206 p50=152 p90=224 p95=264 max=360 (**18% >200s**); 19 routine beats in the 190–208s band.
Matches the auditor within percentile-method noise. The >200s completions are run_duo (no timeout) — valid
counterfactual for the product lanes' 200s kill, same DM model/effort defaults (opus, routine=medium).

**TALLY: 19 confirmed, 2 corrected (F12-2 severity, F12-14 mechanism), 0 refuted.**

---

## F12-1 — CONFIRMED [P1|high|S] Routine-beat 200s timeout kills ~1 in 5 healthy beats; retry reuses the SAME deadline
- Verified: qa/lib_beat_driver.sh:495-506 (`clawdnd_dm_timeout` → flat `worldos_env BEAT_TIMEOUT 200` for first=0).
  scripts/play.sh:278 captures `beat_timeout` ONCE before attempt 1; retry at :313-314 re-invokes `_dm_invoke`
  with the same `$beat_timeout`. play_party.sh:323/349 identical pattern. Measurement reproduced (above).
- Disproof attempts: no escalation path exists anywhere; no env recompute on retry; #748/#761 (merged) is the
  VIEWER backstop, doesn't touch the wrapper deadline; #753 defines the budget but doesn't fix the kill.
- Fix spec OK (wrapper-only, env semantics unchanged, no engine writes): raise routine default 200→≥360 or make
  model-aware like the cold-open tier; recompute attempt-2 timeout (cold-open tier or 2× routine) in play.sh
  dm_turn + play_party turn().
- Test: stub `claude` with sleep-N; assert 250s beat survives + rc=124 retry receives larger timeout argv.
- dup: enriches #753 (wrapper half); #748 viewer backstop separate (and already landed via #761).

## F12-2 — CORRECTED [P1→**P2**|med|S] Sonnet cold-open deadline 400s has thin (not zero) margin; only Opus got the model-aware bump
- Verified: lib:497-502 — `_co_timeout=400; case *opus*) 500`; comment at :499 itself says sonnet max-effort cold
  opens run "~280–400s" — deadline == documented band top.
- Corrections (why downgraded): (1) measured cold-open max here is 370s < 400s — "ZERO margin" overstates; ~8%
  margin vs measured max, zero vs documented band top. (2) The DEFAULT DM model is **opus in every lane**
  (play.sh:48, play_party.sh:74, run_duo.sh:44 — all default opus) → the sonnet arm is an explicit A/B opt-in,
  not the shipped path. (3) A killed cold open gets a retry that #719-resumes the already-minted campaign
  (play.sh:309-311) — typically far cheaper than the full world-build — so the failure is not terminal.
  Does not break a gate today → P2.
- Fix unchanged (one-liner: non-opus cold-open default 400→500/550; optionally retry-escalate per F12-1).
- Test: table-driven unit over CLAWDND_DM_MODEL asserting default ≥ measured-max + margin; pin opus ≥500.
- dup: new.

## F12-3 — CONFIRMED [P1|high|M] play.sh cold open has NO failure abort and NO seating guard
- Verified: play.sh:413-437 — `clawdnd_resolve_dm_reply` then `record_dm_reply "$CAMPAIGN_ID" "$DMSG" opening`
  UNCONDITIONALLY (blank DMSG → log_engine_narration returns 1 → unflagged EMPTY chat row), then enters the move
  loop. play_party.sh:475 has the empty-DMSG abort; :495-532 has pc_seated() + one reseat retry + loud abort —
  play.sh has neither. Double-failed cold open (401 class proven 2026-06-02): CAMPAIGN_ID="" → heartbeat no-ops,
  lean no-ops, every beat `--resume`s a nonexistent session → fails → masks, indefinitely, under a live viewer
  serving the empty state (viewer binds immediately by design, play.sh:327-330).
- Partial-compensation check: the viewer's #746/#761 stuck-backstop covers pending PLAYER moves, not a dead cold
  open (no move pending yet) — does not compensate.
- Fix spec OK (snapshot-read-only guard, invariant-safe; non-zero exit surfaces via the native bridge): port
  play_party's two guards, factor pc_seated() into lib_beat_driver, call in both hero/DM-invents paths.
- Test: always-fail stub → non-zero exit before move loop; world-minted-no-PC replay → reseat fires, 2nd miss aborts.
- dup: new (#721 is the bridge-ABSENT path; #745/#748 are viewer-side).

## F12-4 — CONFIRMED [P1|high|S] play_party.sh missing the model-independent progress heartbeat (#623 never reached the lane it was factored for)
- Verified: grep — `clawdnd_emit_progress_heartbeat` callers = play.sh:385,:488 ONLY (codex has its own inline
  bank, play_codex_dm.sh choose_move_progress_text + log_engine_narration pre-turn). play_party.sh: zero calls;
  it carries only the model-COOPERATIVE rule (l.153/297). lib:312-319 documents the helper as "factored here so
  every harness shares it" + "even when the model SKIPS the cooperative early log_event (Eva measured exactly that)".
- Post-#763 this matters MORE: the viewer now flips progress at heartbeat ingest — the party lane never sends one.
- Fix spec OK (two one-liners; campaign id pre-seeded so even the cold open can heartbeat — better-positioned than
  play.sh; before `companion_moves` so the player isn't staring through companion latency; engine stays sole writer).
- Test: extend test_heartbeat_repair.py wrapper-shape: stubbed party beat + cold open → wrapper-progress row
  timestamped before DM-stub activity.
- dup: new — completes #623; #749 (now MERGED at a245a2c) fixed contamination + ingest-flip, not absence.

## F12-5 — CONFIRMED [P1|high|S] play_party.sh has no soft clock-tick backstop
- Verified: grep — `clawdnd_soft_tick` callers = play.sh:504, run_duo.sh:334, run_duo_openclaw.sh:216 (evidence
  enriched: openclaw duo has it too — play_party is the ONLY beat loop without it). play_party beat loop
  (601-663) never captures PREV_DAY/PREV_TOD and ends at the PREV_LOC capture (:655).
- Fix spec OK (mirror play.sh:475-478/504; helper verified at lib:631-672 — combat guard, defers to DM-advanced
  clock, always returns 0, engine sole writer via advance_time).
- Test: frozen-clock stub beat → one phase advanced; DM-advanced fixture → no tick; combat.active → skip.
- dup: new.

## F12-6 — CONFIRMED [P1|high|M] Director + Event advisories run ONLY in the scored QA lane
- Verified: grep — `clawdnd_director_advisory`/`clawdnd_event_advisory` callers = run_duo.sh:306/312 only, folded
  into the beat prompt at :314-324. play.sh:489-495 and play_party.sh:639-648 inject $RUNBOOK only. Helpers
  verified read-only/non-fatal (lib:816-870; get_campaign_director + present_events never mutate).
- Scoring-provenance claim holds: RRI story/mech numbers come from the duo lane; product players never get the
  advisory levers (the add_quest/present_events reach-for gaps stay open for them).
- Fix spec OK (wire both into play/play_party beat prompts exactly as run_duo; empty ⇒ byte-identical prompt;
  ~2 uv calls/beat ≈ 1-2s vs 100-360s beats). Wiring-forward > stripping-from-duo: agreed (keeps known levers).
- Test: owed-advisory fixture per lane → prompt contains block; nothing-owed → unchanged; duo-vs-play parity assert.
- dup: enriches #643 (scored-lane ≠ product-lane provenance).

## F12-7 — CONFIRMED [P1|high|S] fallback_recovered stamp dead in ALL QA runners — local 3-arg `chatlog` shadows the lib and drops the flag
- Verified at HEAD (post-#763 — the overrides survived the #749 merge): run_duo.sh:135, ui_playtest.sh:138,
  run_party.sh:169 each redefine `chatlog` as `python3 -c '…' "$CHAT" "$1" "$2"` AFTER sourcing the lib
  (l.22/28/46) — clawdnd_chatlog_dm's 3rd arg `'{"fallback_recovered":true}'` (lib:295) silently discarded.
  ui_playtest.sh:181 comment claims the stamp lands — false in that runner.
- Consumer check confirmed: repo grep — fallback_recovered appears ONLY in lib, tests, and comments; zero refs in
  assert_behavioral.py / any qa/*.py → write-only even where it works (play/play_party via record_dm_reply).
- Test-gap mechanism confirmed (A14): test_heartbeat_repair.py:208-271 exercises the LIB chatlog;
  test_dm_session_remint.py:393-401 only source-greps `"clawdnd_chatlog_dm" in src` — cannot catch the override.
- Fix spec OK: lib chatlog verified drop-in superset (reads ambient $CHAT at call time, byte-identical row with no
  3rd arg) → delete the 3 overrides; add the assert_behavioral consumer (count + report; gate policy stays #757's);
  per-runner red-first wrapper-shape test.
- dup: enriches #757 (its fix assumes a stamp that cannot land in its target lanes). #749 is MERGED (a245a2c) —
  this is the remaining dead half, not an in-flight overlap.

## F12-8 — CONFIRMED [P1|high|S] `timeout(1)` is an undeclared coreutils dependency; preflight doesn't check it
- Verified on this Darwin 25 host: `/usr/bin/timeout` ABSENT; only /opt/homebrew/bin/timeout (coreutils).
  play.sh:281 + play_party.sh:329 invoke `timeout "$beat_timeout" claude …`. clawdnd_missing_commands
  (launch_common.sh:26-38) checks `python3 claude uv jq curl` — callers play.sh:33, play_party.sh:63 — no
  `timeout`. ui_playtest.sh:150 names the dep in a comment; repo-wide grep: zero coreutils provisioning.
  Without it: `_dm_invoke` → "command not found" rc=127 in <1s, retry rc=127, empty narration → with F12-3 an
  indefinitely "running" dead session.
- Scope note: preflight already requires brew-ish tools (uv/jq), so the realistic victim is a user with the 5
  listed tools but no coreutils — plausible and silent; fail-loud is cheap.
- Fix spec OK: (1) add `timeout` to both missing_commands calls with a brew hint; (2) better — `worldos_timeout()`
  shim in lib (timeout(1) if present, else python3 subprocess timeout preserving rc=124), swap both call sites.
- Test: PATH-strip test on stubbed dm_turn (red: instant rc=127) → shim enforces deadline; missing_commands assert.
- dup: new ; depends_on F12-3 (the masking half).

## F12-9 — CONFIRMED [P2|high|M] Codex DM wrapper: no retry, no timeout, crash leaves provider_status "running"; budgets validated but unenforced
- Verified: codex_dm_turn (play_codex_dm.sh:628-665) — `codex exec` unbounded, no retry; callers :797-799/:827+
  hard-`fail` on nonzero. fail() (:12-15) exits 2; `_cleanup` trap kills SUP/viewer only — NO write_provider_status
  → sidecar stays `running`. viewer/server.py:5844-5845/5974-5976: no_provider bucket only on
  {stopped,failed,exhausted}. CLAWDND_PLAY_BUDGET/SESSION_BUDGET required+regex-validated (:106-116) then NEVER
  referenced again (grep) — no spend accounting, no over_budget.
- Fix spec OK: EXIT trap writing status:"failed" after "running"; wrap codex exec in the F12-8 shim + ONE retry
  (codex turns stateless → retry session-safe); enforce or drop the budget envs.
- Test: codex stub exits-1-once → survives; sleeps-forever → deadline+retry; kill mid-loop → status reads failed.
- dup: enriches #694 (beat-contract parity); the stale-status half is new.

## F12-10 — CONFIRMED [P2|high|M] Claude lanes never write provider_status.json
- Verified: grep `provider_status` in scripts/ → play_codex_dm.sh ONLY. viewer/server.py:5703-5718 falls back to
  status "unknown" (not in the stopped set → never buckets no_provider). play.sh:448-454 / play_party.sh:576-583
  cap/budget stops echo to a terminal invisible in the .app, then the EXIT trap kills the viewer (no grace render).
- Fix spec OK (sidecar is derived/atomic — not campaign state; invariant-clean): factor write_provider_status into
  lib; call at start/cap-stop(+grace)/EXIT-failed from claude lanes.
- Test: stubbed run to turn cap → provider_status stopped/turn_cap before viewer death; kill -TERM → "failed".
- dup: new.

## F12-11 — CONFIRMED [P2|high|S] run_duo has no per-beat deadline and swallows the real failure cause
- Verified: run_duo.sh turn() dm branch :161-164 unbounded (no `timeout`); turn_retry :177-193 retries only on
  EMPTY output (a hang never returns; an rc≠0-with-output never retries), never calls
  clawdnd_report_attempt_failure (the structured error stays in $out on disk but is never surfaced — the masking
  class lib:540-557 was built for), and re-implements the cold-open remint inline (:185-187) instead of
  clawdnd_dm_remint_session_on_retry.
- Fix spec OK: wrap in `worldos_timeout "$(clawdnd_dm_timeout "$first")"`; report on rc!=0; shared remint; keep
  empty-output retry as second trigger.
- Test: sleep-forever stub → deadline + early-stop; 401 result-event stub → "[dm-attempt] HTTP 401 … NOT retryable"
  on stderr (red-first: absent).
- dup: new ; depends_on F12-8.

## F12-12 — CONFIRMED [P2|high|S] play_party companion turns are unbounded
- Verified: turn() actor branch (play_party.sh:354-360) — bare `claude -p`, no timeout/retry; actor_move nudge
  (:374) same. Comment :321-322 ("the companion facade never gets a per-beat timeout") conflates the no-deadline
  HUMAN with companion MODEL calls. Sequencing confirmed: human move consumed + cursor advanced (:606), then
  companion_moves (:622) blocks BEFORE the DM turn (:639) → acknowledged move, nothing resolves.
- Graceful-degradation path verified: companion_moves tolerates empty `cm` (:411 `[ -n "$cm" ] &&`) — skip is safe.
- Fix spec OK: `worldos_timeout "${WORLDOS_ACTOR_TIMEOUT:-120}"` around the actor branch; empty on failure.
- Test: sleep-forever actor stub → beat reaches the DM within the deadline, companion skipped (red-first: hangs).
- dup: new ; depends_on F12-8.

## F12-13 — CONFIRMED [P2|high|S] play.sh has no idle ceiling and no launch lock
- Verified: play.sh loop :459-508 — no MAX_IDLE (comment :352-353 acknowledges); play_party added
  CLAWDND_PLAY_MAX_IDLE=1800 (:594-599) after the 8.5h orphan. Lock callers grep: play_party.sh:131-132 (AFTER the
  solo `exec play.sh` at :103) + its test only → two solo launches stack two viewers + two DM sessions on the
  16GB OOM-prone host.
- Fix spec OK: copy the MAX_IDLE block; acquire lock before the viewer supervisor / release in _play_cleanup
  (helper verified PID-staleness-safe, launch_common.sh:96-156).
- Test: extend qa/test_play_party_single_flight.sh with a play.sh case; CLAWDND_PLAY_MAX_IDLE=3 idle test.
- dup: new.

## F12-14 — CORRECTED [P2|med|M] Dead-beat masking in the product lanes — mechanism CHANGED at HEAD (#763); the masked/zero-row UX remains
- CORRECTION (the audit's central mechanism is stale on main): #763 (merged, a245a2c) changed
  qa/dm_narration_fallback.py — a wrapper heartbeat now **BREAKS the recovery block** instead of being skipped
  ("a heartbeat-only (dead) beat must recover NOTHING, because stitching the PRIOR beat's stale prose under a
  fresh heartbeat would mask the dead beat as 'resolved'", :138-147). So per-lane on main today:
  * play.sh (heartbeat lane): both attempts die after the pre-beat heartbeat → fallback recovers NOTHING →
    DMSG="" → record_dm_reply with blank text → log_engine_narration returns 1 → **unflagged EMPTY dm row**
    (play.sh:437/:501), spinner flipped by the heartbeat, then nothing arrives. The recycled-prose mode is FIXED
    here; the zero-row mode is the live bug.
  * play_party (NO heartbeat — F12-4): nothing breaks the block → fallback recovers the PREVIOUS beat's prose →
    record_dm_reply dedups it in the last-8 tail (lib:225-247 already=True) → engine_logged stamped → client
    DROPS the row (app.jsx engine_logged===true → null) → recycled-prose-invisible mode STILL live. (Stamp does
    land here — on the hidden row; no consumer.)
  * QA runners: recycled prose rendered as a normal dm row, unstamped (F12-7).
  Note: the viewer #746/#761 stuck-backstop does NOT compensate — the masked beat DELIVERS a (hidden/empty) chat
  row, so "nothing arrived" detection never fires.
- Still needed (fix spec, adjusted): guard record_dm_reply against blank text (skip chatlog, warn); when
  FALLBACK_RECOVERED=1 AND the prose dedups as already-logged, emit a wrapper-authored VISIBLE failure beat
  through the engine + stamp {"beat_failed":true} (coordinate UX with #757/#745); preserve the genuine #357 win
  (NEW prose logged then died — detectable via pre-beat log-tail snapshot). After F12-4 lands, play_party
  converges to the empty-row mode and the same guard covers it.
- Test: pre-beat log has P1, both attempts fail → chat gains a visible failure row (not P1, not empty, not
  hidden), logged exactly once; DM logs NEW P2 then dies → P2 used and rendered.
- dup: enriches #757 + #745 (lane generalization + the zero-row wrinkle + the engine_logged-drop interplay).

## F12-15 — CONFIRMED [P2|high|S] player_server re-resolves "the live campaign" on EVERY tool call
- Verified: servers/engine/player_server.py:53-62 — `_campaign()` = max(updated_at) per call; with ACTOR_ID set,
  _pc() (:94-109) returns None when the actor id isn't in whichever campaign is currently freshest → moves refused
  ("no character yet") → ACTOR_ID-bound companion goes silent when a parallel campaign takes the lead. The wrappers
  fixed this selector class via store.active_campaign_id (#640, lib:53-59 documents why); the facade kept the
  heuristic (its H3 comment guards dir-order staleness, not divergence).
- Fix spec OK (additive env pin CLAWDND_CAMPAIGN_ID, unset → byte-identical heuristic; wire first in play_party
  companion cfgs :262-269 where the id is known at config-write time). Invariant-2 safe, read-only.
- Test: red-first two-campaign fixture (A w/ companion X, fresher B): ACTOR_ID=X+CAMPAIGN_ID=A → my_sheet resolves X.
- dup: enriches #640.

## F12-16 — CONFIRMED [P3|high|S] play_party ensemble ignores WORLDOS_* model envs; viewer env block exports only CLAWDND_*
- Verified: play_party.sh:74-75 plain `${CLAWDND_DM_MODEL:-opus}`/`${CLAWDND_ACTOR_MODEL:-sonnet}` (siblings use
  worldos_env: play.sh:48, run_duo.sh:44/47); viewer_supervisor :424-426 exports CLAWDND_{STATE_DIR,VIEWER_CHAT,
  PLAYER_MOVES} without WORLDOS twins (play.sh:338-341 + codex export both). Impact-today check confirmed none:
  viewer/server.py reads via env_var() with legacy fallback (:134-159, :7536) — breaks only the #295 forward
  contract + WORLDOS_DM_MODEL A/B ergonomics in party mode.
- Fix/test as filed (worldos_env swap + twin exports; red-first WORLDOS_DM_MODEL=sonnet → party argv).
- dup: enriches #295.

## F12-17 — CONFIRMED [P3|high|S] Seed-screen moves render as "[set_seed_param] " in party and codex lanes
- Verified: viewer emits {kind:"set_seed_param",param,value[,force]} (viewer/server.py:3278). play.sh:467 has the
  jq branch; play_party.sh:608 and play_codex_dm.sh:818 use the plain `"[\(.kind)] \(.text // .name // "")"` map —
  param/value dropped; play.sh:493 also carries the DM-side instruction the party prompt lacks.
- Fix/test as filed (copy the jq branch + prompt line; red-first row-through-jq in 2 lanes).
- dup: new.

## F12-18 — CONFIRMED [P3|high|S] play_party 30-min campaign-reuse silently ignores a CHANGED companion spec
- Verified: play_party.sh:184-240 — reuse branch harvests existing companions (persona hardcoded
  "qa/play_companion.txt"); create loop is `spec.split(",") if _minted else []` → requested-but-missing companions
  never created on reuse.
- Fix/test as filed (diff requested vs existing; create missing / or reuse only when requested ⊆ existing).
- dup: new.

## F12-19 — CONFIRMED [P3|high|S] validate_attack: unstripped weapon compare + empty-inventory bypass
- Verified: player_server.py:276 — `weapon.lower()` (no strip) vs owned_items() which strips+lowers (:204-205) →
  `"sword "` false-refused; `and owned_items(pc)` → empty-inventory PC attacks with any named weapon unvalidated,
  contradicting the docstring (:267-273).
- Fix/test as filed (strip; drop or document the emptiness clause; two validator units).
- dup: new.

## F12-20 — CONFIRMED [P3|high|M] Consolidation bundle (drift-prone duplicates + the over_budget re-slurp)
- Verified: (a) play_party.sh:153 re-defines CLAWDND_LIVE_PROGRESS_RULE after sourcing the lib — programmatic diff
  this review: byte-identical TODAY (pure shadow, will drift); (b) run_duo.sh:185-187 inline remint duplicates the
  shared helper; (c) codex carries local chatlog (extra_json-capable, verbatim-lib) + its own record path
  (no-idempotency append deliberate); (d) viewer_supervisor triplicated (play.sh:336-347, play_party:422-431,
  codex:~700-712) with F12-16's env drift; (e) over_budget re-slurps the ever-growing $COMBINED via `jq -rs` on
  EVERY loop pass — including each 2s idle tick (play.sh:459-460→448-453, play_party:601-602→576-581); (f) play.sh
  dm_turn ≈ play_party turn() dm branch ~90% identical — F12-1/2/4 would otherwise be patched twice.
- Fix/test as filed (clawdnd_viewer_supervisor + clawdnd_dm_invoke + incremental session-spent cursor; shape guard
  on single rule definition). Sequence with F12-1/2/4/11.
- dup: new.

## F12-21 — CONFIRMED [P3|med|S] Authored/canon hero pre-seed failure silently downgrades to DM-invents
- Verified: play.sh:220 — empty HERO_SEED_JSON → one stderr line ("falling back to DM-invents-PC"), session
  proceeds; in the .app the player picked canon hero X and silently gets a different PC. (The CLAWDND_PLAY_HERO
  block only runs when the spec was explicitly set — so ANY pre-seed failure here is a player-visible identity
  break.)
- Fix/test as filed (fail the launch loudly on explicit spec; red-first nonexistent-canon launch → non-zero exit).
  Confidence med stands (owner may prefer substitute + in-chat notice).
- dup: enriches #721 (silent-bind-failure class).

---

## CLEAN-VERIFIED (carried; items 1,5,6,7,8,11,12 spot-re-verified at HEAD this review)
1. Single-flight lock (launch_common.sh:96-156): atomic mkdir + PID staleness + race wait + owner-only release —
   correct; covered by qa/test_play_party_single_flight.sh. 2. bash-3.2 heredoc/array discipline holds. 3. %s%N
   real nanoseconds on this host. 4. effort/timeout/lean keyed off one `first` signal; lean inversion fixed.
5. remint: lean self-mints; shared helper only re-mints --session-id mode; #719 retry-resume correctly gated +
   read-only (play.sh:309-311). 6. record_dm_reply idempotency + heartbeat dedup-exemption (#720/#727/#749d)
   verified at lib:208-300; fallback filters wrapper lines AND (new, #763) breaks the block on them. 7. soft_tick
   combat guard correct (lib:644-646). 8. over_budget awk math + opus-aware budget defaults + duo $4 floor correct.
9. player_server security boundary (read-only, role clamp, dead/NPC refusal, slot validation, skill normalization)
   holds; facade surface gaps already tracked #594/#599/#617-#621. 10. duo root/IS_SANDBOX preflight loud.
11. file-based move cursors subshell-safe; trust boundary (structured moves only) holds in both ensembles.
12. viewer supervisors wait-and-reap; INT/TERM traps cleanup AND exit in all three lanes. 13. codex input hygiene +
    frozen wire contract (clawdnd-* ids, CLAWDND_* env) intact; atomic provider_status writes. 14. worldos_env
    precedence + once-per-run sentinel works in subshells. 15. duo qa.mcp re-rooting + alwaysLoad pin correct.

## VERDICT (amended)
The auditor's drift thesis SURVIVES skeptical review almost intact: 19/21 confirmed at HEAD, with the measurement
independently reproduced. Two corrections: F12-2 downgrades to P2 (opus is the default everywhere; the sonnet
band-top margin is thin, not zero; #719 retry-resume bounds the blast radius), and F12-14's mechanism must be
restated post-#763 (play.sh now fails to an unflagged EMPTY row, not recycled prose; the recycled-invisible mode
persists exactly where the heartbeat is missing — play_party — making F12-4 + F12-14 a coupled pair). The #749c
honesty stamp being dead in every QA runner (F12-7) is the highest-leverage small fix: #757's planned gate
discount is unimplementable until the three local chatlog overrides are deleted.

════════════════════════════════════════════════════════════════
## UNIT 13 — VERIFIED REPORT (verbatim from unit-13-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 13 — CROSS-CUTTING LATENCY FORENSICS — SKEPTIC-VERIFIED
Verified: 2026-06-11 against /Users/lume/ClawDnD-val @ HEAD **a245a2c** (audit was @ f24a102; HEAD moved +3 commits — notably a245a2c = the #749/#763 heartbeat-decontamination fix touching server.py, dm_narration_fallback.py, sweep_v2.sh — every finding re-checked at HEAD).
Auditor's full report: /tmp/engine-audit/unit-13-audit.md. Verdict: **6 confirmed, 2 corrected, 0 refuted.** The auditor's measurements were byte-exact reproducible (docstrings 89,713B, roster 180,630B, MISS 28,187B, HIT 271B — all re-measured identical at HEAD); credibility high.

## SKEPTIC METHOD / WHAT WAS RE-CHECKED
- Re-ran the engine measurements at HEAD (uv run, tmp state dir, real baldurs-gate): 141 `@mcp.tool` confirmed (`grep -c`); `mcp.list_tools()` JSON = **175,202B in my serialization** vs auditor's 159,936B (FastMCP wire format) — same ~40K-token order, auditor's figure is the *conservative* one; docstring ast-scan = 89,713B **exact match**; list_canon_characters = 180,630B **exact** (roster now 2,076 records, was 2,074 — content drift, immaterial); load_canon MISS = 28,187B **exact**, HIT 271B.
- Opened every cited file:line at HEAD: server.py:2274-2289 (list_canon — no limit/filter; note `find_npcs` immediately below it ALREADY has `limit: int = 50`, an in-file precedent for the fix), :2360 (MISS roster dump), :1550 (pickup miss, `"playable": avail`), :1527 (template miss), :7214 (log_event echo), :8906-9020 (_scene_durable_threads — verified NO cap on npc_relationships or open_quests, and the #763 commit did NOT add one), :9251 (persist_beat "logged" echo). alwaysLoad: play.sh:115, play_party.sh:161, run_duo.sh:109 ✓ (env-gated `CLAWDND_ENGINE_ALWAYSLOAD=1` default).
- Re-ran the Mac beat-loss census at HEAD: archives grew since the audit (294 files vs 269): **28 no-result + 3×401 / 294 ≈ 10.5%** — the ~10% claim HOLDS with drift. Opened the cited 401 sample (play-state/play-20260602022602/dm.1780367162967717000.jsonl): `subtype:"success", is_error:true, api_error_status:401, duration_ms:1164, result:"Failed to authenticate. API Error: 401 …"` — verified verbatim.
- Checked qa/scores_db.py:80-110 (COLUMNS — zero latency fields) + _ensure_schema ALTER-add path ✓; qa/distill.py parses duration_ms/num_turns/cost from result events ✓ (minor: it does NOT parse duration_api_ms — the rollup must parse raw events, which the spec already does).
- Checked the in-flight surface: #757 body explicitly says "Fix in flight (heartbeat-repair PR) … follow-up: discount flagged rows in behavioral tallies" — #763 IS that in-flight PR (landed at HEAD), and the follow-up classification work is exactly what F13-5 enriches. #753 body confirmed = budget definition (F13-1/F13-4 enrich it). All dup_statuses re-checked against /tmp/engine-audit/open-issues.txt.
- Invariant check on every fix spec: F13-1 split keeps all writes in the same process/module under campaign_lock (sole-writer ✓), existing `clawdnd-*` ids/env untouched + a NEW additive id (wire ✓); F13-2 `limit:int=0` default = today's dump (additive ✓); F13-3/F13-7 are return-payload-only (no snapshot impact ✓); F13-6 is pure derivation, no state writes, snapshots untouched ✓ — but its default-cap IS a DM-visible behavior change (noted below). No engine-rolls surface touched anywhere.
- Clean-verified list: spot-checked #3 (clawdnd_dm_effort_arg present in all 3 runners ✓) and #4 (run_duo.sh:70 `CLAWDND_LEAN_BEATS:-1`, sweep_v2.sh `export CLAWDND_LEAN_BEATS=1` with "#683/#685-fixed" header ✓ — the repo supersedes the stale 2026-06-05 lean-OFF guidance that still lives in the worldos-latency-forensics SKILL.md; flagged as doc-drift, separate task).

---

## F13-1 — CONFIRMED [P1 | high(measurement)/medium(wall-clock delta) | M]
**alwaysLoad pins a ~40K-token engine tool-schema surface into every DM request — ~54% of the per-beat input floor.**
- Root verified at HEAD: 141 `@mcp.tool()` in servers/engine/server.py; list_tools JSON 160-175KB (serialization-dependent, both ≈40-44K tok); docstrings 89,713B exact; `"alwaysLoad": True` per-SERVER at play.sh:115 / play_party.sh:161 / run_duo.sh:109. No tool-level granularity exists. 73.7K-floor share (VM vm_ctx.json, n=105) not independently re-derived but arithmetic is consistent (40K schema + ~9.6K SKILL.md + briefs + harness system prompt).
- Fix spec OK with ONE skeptic addition: the deferred-tail split must be **usage-ranked with the COLD-OPEN loop in mind** — start_world / get_prelude / get_quest_hooks / spawn_monster / generate_image are cold-open-path tools; deferring them re-introduces ToolSearch round-trips inside the 22-turn cold-open (the give-up band #745/#748 polices). Either keep cold-open tools pinned or accept+measure the few ToolSearch turns there. Otherwise spec is invariant-clean (same process/store, campaign_lock, additive server id).
- depends_on F13-4 (A/B ledger); dup: **enriches #753** (open ✓).

## F13-2 — CONFIRMED [P1 | high | S]
**list_canon_characters returns ~45K tokens (180,630B re-measured exact; 2,076 records) — harness offloads to a tool-results file, DM Read-pages it back at cold-open.**
- server.py:2274-2289 verified: no limit/q/pagination. playable_only barely helps (173KB). The Read-paging evidence (5+5+4 pages, 18 in-transcript 1,343B truncation stubs) is the auditor's transcript work — mechanism consistent with the verified 180KB return; accepted.
- Skeptic note strengthening feasibility: `find_npcs` directly below (server.py:2291+) already ships `limit: int = 50` — the fix is an established in-file pattern.
- Fix spec invariant-clean (limit=0 default = today's behavior; consumers flipped via prompt). dup: **new** (#316 = viewer roster UI, different surface — cross-link only).

## F13-3 — CORRECTED [P2 | high | S]
**load_canon_character MISS dumps the full roster (~7K tokens / 28,187B re-measured exact) as its error payload — at TWO sites, not three.**
- CORRECTION: the audit listed 3 sites. Verified: server.py:2360 (load miss → ALL 2,076 names) and :1550 (pickup miss → all playable names) are real and obese. **server.py:1527 (origin template miss) is NOT obese — only 7 templates exist, the `available` list is 134B.** Including it in the did_you_mean refactor is harmless consistency, but it is not evidence and not a latency site; the finding's scope is the two roster sites.
- Fix (difflib top-10 + substring + total_roster + hint) is error-path-only, invariant-clean. Test spec stands (fails today on size). dup: **new**.

## F13-4 — CONFIRMED [P1 | high | S-M]
**scores_db has no latency columns — the #753 budget has no ledger.**
- qa/scores_db.py:80-110 verified at HEAD: COLUMNS is quality/provenance only; `_ensure_schema` ALTER-adds additively (verified code path) → migration-free. run_duo.sh turn() writes `$T/$RUN.dm.<ns>.jsonl` per beat ✓. Minor evidence trim: qa/distill.py parses duration_ms/num_turns/cost but NOT duration_api_ms — the new rollup parses raw result events itself (spec already says so), so no change.
- Skeptic reinforcement: the worldos-latency-forensics skill ALREADY mandates "Record latency columns (s/beat, cold-open-s, turns/beat) to qa/scores_db.py on every run" — this finding is the missing implementation of an existing project rule, which justifies P1.
- Column set + `_REAL_COLS`/`_INT_COLS` registration + cold-open-excluded percentiles: spec is sound. dup: **enriches #753**.

## F13-5 — CORRECTED [P2 | high | S]
**~10% of DM invocations produce no usable beat — and the 401 path is WORSE than the audit stated: the raw auth-error string flows to chat as DM prose.**
- Census re-run at HEAD: Mac archives grew to 294 files → **28 no-result + 3×401 ≈ 10.5%** (audit said 16+3 of 269 — archives gained runs since; claim holds). 401 sample verified verbatim (subtype:"success", is_error:true, 1164ms).
- ROOT-CAUSE CORRECTION (mechanism sharpened): the 401's `result` field is **non-empty** error text. `turn_retry` (run_duo.sh:176-190) retries only on EMPTY; `clawdnd_resolve_dm_reply` (lib_beat_driver.sh:138) falls back only on EMPTY. So the 401 error string **bypasses both the retry AND the fallback** and is chatlogged as the DM's reply ("Failed to authenticate…" as narration). The "fallback dresses it as resolved" mechanism applies only to the no-result/empty path. Post-#763 (HEAD) the fallback honestly stamps `fallback_recovered:true` and recovers nothing from heartbeat-only dead beats — but no path classifies the beat FAILED, surfaces re-auth, or counts it.
- Fix spec corrected accordingly: in the shared resolve path, parse the final result event FIRST — `is_error`/`api_error_status` (401 et al.) ⇒ beat FAILED (never chat the error text, never fallback-recycle), surface "DM needs re-auth", count into F13-4 `beats_failed`; budget pre-check before launch. Test: 401 fixture → beat recorded failed, chat row contains neither the error string nor recycled prose.
- dup: **enriches #757** (open; its body names exactly this follow-up: "discount flagged rows in behavioral tallies" — the masking itself is NOT re-filed). depends_on: #757.

## F13-6 — CONFIRMED (evidence caveat) [P2 | high(root)/medium(timing) | S]
**scene_context `durable` block grows unbounded — no cap on npc_relationships (every met NPC forever) or open_quests.**
- server.py:8906-9020 verified at HEAD: plain `for` loops, no slice, no relevance sort; the #763 commit touched the adjacent `_scene_recent_narration` (heartbeat filter) but added NO caps to the durable block.
- EVIDENCE CAVEAT (skeptic): the "+60% within 8 beats" (8,145B bench → 14,407B transcript max) conflates durable growth with the `recent_narration=8` prose tail, which is the likelier short-run growth driver. The uncapped-accumulation root cause is code-fact regardless; the 30-60KB long-campaign projection is directionally sound (met-NPCs and open quests only accumulate) but unmeasured — hence impact-timing stays medium.
- Fix-spec invariant check: pure derivation, no state writes, snapshots untouched, LOSSLESS RULE honored via omitted-counters + recall-on-demand ✓. NOTE: the default N=24 cap is a DM-visible behavior change on large campaigns (the red-first test asserts the new default) — fine, but the duo A/B on a long-campaign fixture should ride with it, and open_quests stay complete (correct — they are the OWED list).
- dup: **new** (#749 closed by #763 = what enters the log; this = how much the spine carries — distinct).

## F13-7 — CONFIRMED (scope note) [P3 | high | S]
**log_event — most-called tool (195 calls) — echoes the DM's own prose back.**
- server.py:7214 verified at HEAD: `{"session_id", "logged": entry.model_dump()}`. Consumer grep: NO non-test consumer reads the echo (viewer reads session files); engine tests do not assert on log_event's `logged` key.
- SCOPE NOTE (skeptic): **persist_beat (server.py:9251) echoes the same way** (`"logged": [...]` with full entries — deliverable C rank 9, p90 5,916B) and IS test-asserted (test_beat_roundtrip.py:307/329/346). The cheap fix is log_event-only as specced; if persist_beat is trimmed too, those three assertions must be updated — keep it a separate, explicitly-scoped commit or leave it.
- dup: **new**.

## F13-8 — CONFIRMED [P3 | medium | S-M]
**Cold-open Reads two skill reference files (~27KB) as separate turns every session.**
- Root verified: SKILL.md:85 "**Read this at the START of every session**" (storycraft.md, 17.3KB) + :89 "**Read this whenever you `start_world`**" (quest-generation.md, 9.5KB); SKILL.md:83 instructs the repo-root Read path. Spot-grep: storycraft.md appears in 60 / quest-generation.md in 56 of 294 Mac transcripts — consistent with the per-cold-open claim (the grep is an upper bound; the auditor's tool_use-path count of 40/39 over 41 cold-opens is the precise figure).
- Fix feasibility verified: the `first`-keyed branch exists (clawdnd_dm_effort_arg, lib_beat_driver.sh:454) and the `--append-system-prompt` machinery exists for lean continuing beats (:421) — but the COLD-OPEN currently has NO --append-system-prompt, so the card is a new (small) arg path in the shared helper, applied to all 3 runners. Effort S-M and medium confidence (distillation quality risk on north-star story rules) are honest — keep.
- dup: **new**.

---

## CLEAN-VERIFIED (skeptic re-checked)
1. Engine hot path innocent — bench reproduced at HEAD (worst call 161ms-class; list_canon 180KB/161ms is a SIZE problem, not a time problem); "engine ≈1-4%/beat" stands.
2. alwaysLoad (#574) works — wiring verified in all 3 runners (env-gated, default on).
3. Effort tiering (#551) — clawdnd_dm_effort_arg present in play.sh / run_duo.sh / play_party.sh ✓ (#561 covers the OTHER runners — no conflict).
4. Lean-ON production+QA default — verified in-repo (run_duo.sh:70 default 1; sweep_v2.sh `export CLAWDND_LEAN_BEATS=1` "#683/#685-fixed"). The stale "lean is BROKEN/OFF" text in the worldos-latency-forensics SKILL.md is repo doc-drift (flagged separately) — the AUDITOR was right, the skill is behind.
5. Combat returns lean ✓ (bench).
6. Wall-clock trend right direction (lever bundle, not controlled A/B — caveat retained).
7. Non-API outliers = rate-limit backoff (cited file verified to contain the rate_limit_event claim structure).
8. scores_db additive schema ✓ (code-verified ALTER path).

## CAVEATS RETAINED + ADDED
- VM duration_api_ms > duration_ms rows: clamp at 0 (parallel segments). Opus n=12 too thin. Era comparison = config+hardware confound.
- ADDED: archives are live — censuses drift run-to-run (Mac 269→294 files during this audit cycle); F13-4's ledger is what makes these numbers stop rotting.
- ADDED: CLAWDND_STATE_DIR is deprecation-warned in favor of WORLDOS_STATE_DIR (#295 rename in flight) — any new env knob in these fixes should use the WORLDOS_ prefix.

## VERDICT (unchanged in substance)
Engine exonerated; the per-beat tax is the ~74K-token fixed input floor (~54% = 141 pinned tool schemas) + a few obese returns (45K-token roster, 7K-token roster-as-error) + an unledgered latency program. Baseline (current config, Sonnet): routine p50 80s/p90 118s; cold-open p50 174s/p90 239s.

════════════════════════════════════════════════════════════════
## UNIT 14 — VERIFIED REPORT (verbatim from unit-14-verified.md)
════════════════════════════════════════════════════════════════

# UNIT 14 — API SURFACE / TOOL CONTRACT — SKEPTIC-VERIFIED (v3)

Verified at repo /Users/lume/ClawDnD-val, HEAD a245a2c (6 commits past the audit base f24a102; f24a102 is an ancestor).
The delta (#760–#764) touched recap.py + server.py (_scene_recent_narration) — ONLY to filter the #749 wrapper-heartbeat
line; no size caps were added, so the sizing findings survive the drift. Line numbers below are re-cited at HEAD.

Method: every code citation re-opened at HEAD; every measurement re-run where cheap (tools/list mass re-measured EXACT;
canon roster re-measured EXACT; recap unboundedness reproduced live; bigrams re-counted EXACT from the auditor's own
artifact + spot-checked raw transcripts); every transcript quote pulled verbatim; dup check against
/tmp/engine-audit/open-issues.txt; fix-specs checked against engine invariants (sole-writer, additive/_StrictModel
round-trip, engine-rolls, frozen wire contracts).

VERDICT TALLY: 14 confirmed, 6 corrected, 0 refuted.

---

## F14-1: load_canon_character miss dumps the ENTIRE 2,076-name roster; list_canon_characters 180KB unfiltered — CONFIRMED (P1, high)
- Re-verified at HEAD: server.py:2360-2361 (miss → `available` = full roster names), 2275-2289 (list tool, no
  limit/name_contains), content.py:155-197 (uncapped walk). `ls content/worlds/baldurs-gate/characters | wc -l` = 2076.
- RE-MEASURED LIVE: full list = 180,630B EXACT; names-only miss payload = 28,148B (transcript-observed 36,469B includes
  the error string; census confirms load_canon_character max=36,471B). list_canon_characters census mean 1,343B = QA
  fixture worlds, exactly why gates never felt it.
- find_npcs: ZERO lifetime calls (census artifact re-queried). Neither docstring cross-links it — verified.
- Transcript: gate-duo2 cold open persisted the roster to a file and Bash-grep'd it (I count 6 grep'd Bash calls in the
  dm transcript vs the auditor's 9 — immaterial; duration_api_ms=313,700 verified EXACT).
- Disproof attempts: no consumer of the miss-path `available`/`playable` keys found in scripts/play.sh, qa/run_duo.sh,
  lib_beat_driver.sh (play.sh checks only `error` — keep that key). No in-flight issue owns it.
- Fix spec OK (resolve-then-suggest, copy lookup_item's `suggestions` at server.py:5825-5831; bound start_character's
  template/playable miss at 1526-1527/1549-1550 too). Invariant-clean: additive, no renames.
- dup: new. effort S.

## F14-2: schema-skew pydantic wall — CORRECTED: P1 → P2; spec trimmed (med confidence)
- Mechanism at HEAD CONFIRMED: store.py:139-170 — first-attempt ValidationError swallowed, tolerant net strips unknown
  TOP-level keys only, final `raise RuntimeError(f"…Validation error: {exc}")` embeds the full multi-KB wall; sub-model
  extras still fail all the way (census: 10,040B walls across ≥6 tools, samples verified).
- REFUTING CONTEXT the audit underweighted: (1) the tolerant net (0173ae2) PREDATES the wall transcripts — the observed
  walls came from a STALE SIBLING CHECKOUT running pre-net/pre-field code (run_duo.sh:80-90's own diagnosis; the harness
  now re-roots every server at $ROOT, which is the actual fix that landed); (2) schema_version/engine_sha/calendar are
  top-level keys the HEAD net absorbs. So the OBSERVED wall class cannot recur via the QA harness today.
- Residual exposure (real): sub-model skew (old engine reading a newer save whose SUB-model gained a field) still
  produces the verbatim wall via the RuntimeError. Not gate-breaking today → P2.
- SPEC CORRECTION (invariant): DROP the "extend the tolerant net one level into sub-models" option — store.py:142-145
  states sub-model strictness is INTENTIONAL, and warn-and-drop inside Character risks silent data loss on the
  round-trip invariant. Keep: final-except → SnapshotSchemaError, ≤500 chars, skew direction + both SHAs + campaign id +
  first 3 error locs + 2 recovery moves. Test: bumped sub-model snapshot → <500 chars, no errors.pydantic.dev URL.
- dup: new (distinct from #757, which is wrapper-side masking). effort S/M (down from M).

## F14-3: persist_beat non-atomic / chosen:null crash / bare KeyError / quadratic echo — CONFIRMED w/ evidence correction (P1, high)
- All four code claims re-verified at HEAD (persist_beat now 9141-9259): events → _log_session_entry → store.append_log
  (137-148 + store.py:328-332) writes the session jsonl IMMEDIATELY, before memories/decision validation;
  `decision.get("chosen","")` passes explicit null → Decision.chosen: str (models.py:1102-1106) string_type crash —
  same latent class on summary/rationale; `mem["character_id"]` bare-KeyErrors; `remembered.append({…"memory":
  ch.memory})` echoes the whole growing list per item (quadratic).
- Transcript verified VERBATIM (gate-duo2.dm.1780189864213756000): call 1 memories character_id="maddala-deadeye" →
  "no character 'maddala-deadeye' in campaign" (+ award_xp same); call 2 correct id but chosen:null → "1 validation
  error for Decision\nchosen\n Input should be a valid string [string_type, input_value=None]"; call 3 succeeds.
- EVIDENCE CORRECTION: all three observed calls carried events=0 — the orphan-row/duplicate-chronicle leg is code-real
  but NOT demonstrated by the cited run. Also: memories/decision are ALREADY fail-atomic (load is fresh-from-disk, no
  process cache; save_campaign only at block end — a raise discards in-memory mutations). The non-atomic window is
  EVENTS ONLY. Fix spec (validate the whole batch before the first append_log, per-item failure report, slim
  `remembered` rows, advance stays sequential) stands and is invariant-clean.
- Kept P1: flagship composite burned ~2 beats (~100s each) in a real gate run; two crash classes are still live.
- dup: new. depends_on F14-8 (call 1 IS the F14-8 failure). effort M.

## F14-4: DM-scheduled consequences structurally never fire — CONFIRMED (P1, high)
- Re-verified: `consequences_mod.due()` call sites = server.py:7312 (check_consequences) + 7947 (downtime) ONLY (full
  grep). Census: BOTH tools ZERO calls in 345 files; advance_time ×24 / travel_to ×90 tick worldsim but never due();
  scene_context durable omits pending consequences (pending() only at 7322/7931). SKILL.md:94 mandates
  check_consequences — zero adoption proven.
- Disproof attempts: no worldsim/travel path fires due() (grep over server.py, worldsim.py, travel.py); due() marks
  fired (consequences.py:25-37) so the seam-fire spec is idempotent — double-fire concern resolved IN the engine.
- Fix spec invariant-clean (engine fires inside existing lock+save; additive `consequences_due` key; check_consequences
  kept). dup: new. effort S/M.

## F14-5: travel_to bounces 18% with no route hint — CONFIRMED (P2, high)
- Re-verified: travel.py:88 (unknown id, no did-you-mean), 97-102 (rejection lists direct exits only, no BFS though the
  engine holds the full connection graph). Census: 16 errors / 90 calls EXACT. P2 stands (transcript shows next-call
  recovery via listed exits — the auditor's own downgrade was correct).
- Spec note kept: via_route=True must document per-leg seam behavior (one wandering-encounter roll per leg vs per call)
  — design decision, not a blocker. dup: new. effort M.

## F14-6: alwaysLoad pins 175,202B ≈ 43.8K tokens of schemas per beat — CONFIRMED measurement; SPEC CORRECTED (P1)
- RE-MEASURED LIVE at HEAD: `mcp.list_tools()` serialization = 141 tools, 175,202B — EXACT match. Wrapper pins
  re-verified: run_duo.sh:103-109 (engine alwaysLoad, A/B env flag), play.sh:115, play_party.sh:161.
- SPEC CORRECTION (feasibility): docstring-tiering the dead tail alone CANNOT reach ≤110KB — dead-tail doc_chars total
  ≈44KB; trimming to ~150 chars saves only ~30KB → ~145KB. The remaining dead-tail mass is inputSchema overhead, which
  only unpinning/deferring the dead tail (or a second facade) removes. So the "per-tool pinning only if needed" rider is
  actually the load-bearing half of the ≤110KB target. Revised spec: (1) docstring-tier the dead tail (~30KB, safe),
  (2) CI byte-budget test, (3) split alwaysLoad to the live ~53-tool surface with the tail deferred — gated on a duo A/B
  (ToolSearch hops + api_ms) per latency-forensics discipline; never rename the frozen clawdnd-engine server id.
- Confidence: high on mass / med on seconds (unchanged). dup: new — engine-side lever toward #753, not a duplicate.
  depends_on F14-9. effort M.

## F14-7: cast_spell refusals omit what the caster CAN cast — CONFIRMED (P2, high)
- Re-verified at HEAD: server.py:5340-5341 (`known` computed then unused in the raise), ~5351 (slot refusal hides the
  slot table), 5289 (unknown spell, no fuzzy). Transcript verbatim: "Rolan doesn't know or have 'Thunderwave' prepared"
  then same for 'Misty Step' (str2-adversarial-b). Census 4/55. Spec ≤300B additive — invariant-clean. dup: new. effort S.

## F14-8: `_char` bare raise, no did-you-mean, ~60 tools inherit — CONFIRMED (P1, high)
- Re-verified: server.py:123-127 dict-get-and-raise. The maddala-deadeye double-failure (persist_beat + award_xp) is
  verbatim in gate-duo2. In-repo proof both fix patterns exist: travel.py exits error, lookup_item suggestions.
- Ambiguity guard in spec (duplicate names → raise, never resolve, no mutation) is the right sole-writer-safe shape.
- ABSORBS F14-20 (see below). dup: new. effort S.

## F14-9: cold-open is a 6–9 serial-call ritual; no session-open composite — CONFIRMED (P2, med)
- Bigrams re-counted EXACT from artifact: start_world→list_canon ×27, start_session→look_around ×22,
  look_around→get_quest_hooks ×28; speak→speak ×80. duo2 cold beat duration_api_ms=313,700 verified. start_world census
  mean 11,889B verified; start_session 146B verified (it DOES return a recap — 146B is the fresh-campaign empty-recap
  case, fine for cold opens). Composite spec is additive with parity test. dup: new. depends_on F14-1, F14-5. effort M.

## F14-10: clawdnd-rules ZERO calls vs SKILL.md mandate — CORRECTED: P1 → P2 (med)
- Facts re-verified: census ZERO rules calls in 345 files; SKILL.md:14 mandates rules lookups for "every rule, spell,
  monster stat, or condition"; run_duo/play pin engine-only alwaysLoad (rules present but deferred).
- DOWNGRADE rationale: the v2 upgrade to P1 leaned on "mech 3.0 vs 4.5" — but the finding's own fix step 1 is "score one
  gate transcript's rulings vs SRD FIRST; if misses aren't real, demote the SKILL rule". A finding whose fix begins with
  establishing whether there is damage is not P1 (house rule: validate the domain before extending the protection).
  Engine tools already compute modifiers/attack lines authoritatively; the marginal value of rules lookups is exactly
  what the scoring pass must establish. Keep the two-branch spec verbatim; run the domain check before any wiring.
- dup: enriches #166 (open, verified). effort S–M.

## F14-11: update_character — in_party untranslated; raw pydantic spew; all-or-nothing patch — CONFIRMED (P2, high)
- Re-verified at HEAD: server.py:2596-2620 translates exactly five aliases; in_party has ZERO handling (and is NOT a
  Character field — membership is c.party, returned as computed `ch.id in c.party` at 1633/1683/2541); bare
  `Character.model_validate(data)` propagates raw walls; return = full sheet model_dump. ow-duoF-112226 transcript
  contains the in_party input — verified present.
- Spec OK (pop in_party → c.party mutation mirrors recruit/dismiss; wrapped one-line ValueError keeps typo-forbid red).
  dup: new. effort S.

## F14-12: learn_spells/prepare_spells wholesale REPLACE — CONFIRMED w/ one claim correction (P3, high)
- Re-verified: server.py:6257-6276 wholesale replace, no add/remove, vs inventory's add_item/remove_item asymmetry.
- CORRECTION: the docstrings DO disclose it — "Set … (replaces the list)" — so "one-line docstrings hide it" overstates;
  the defect is the missing add/remove affordance + no `dropped_by_replace` receipt, not concealment.
- dup: enriches #754; ALSO coordinate with #617 (prepare_spells move-kind) and #610 (RestModal → prepare_spells) — both
  open and about to make this surface load-bearing. effort S.

## F14-13: two error channels; soft errors invisible to the gate — CONFIRMED (P3, high)
- Re-verified: dict-shaped errors at server.py:1526-1527, 1549-1550, 2360-2361 + the dead-PC path (~2370-2380);
  assert_behavioral.py parses is_error only (lines 62-98, 507). Census-confirmed: the 36KB roster dump is is_error=false.
  Spec (write the convention down + report-only tool_soft_errors counter) is the right size. dup: new. effort S.

## F14-14: echo-back returns — CONFIRMED (P2; high on mass, med on zero behavior-dependence)
- Re-verified at HEAD: log_event:7212-7214 returns full entry dump; remember/forget return entire ch.memory (O(n) per
  append); persist_beat remembered quadratic (re-confirmed in F14-3); update_character returns 4KB sheet for a 2-field
  patch (:2653-2655).
- Disproof attempt: searched for consumers of the echoes — assert_behavioral's "logged" hits are unrelated prose; the
  viewer tails session-log FILES, not tool returns; no harness parses these returns. Spec's one-gate-duo-before-merge
  guard is the right protection for the med-confidence leg. dup: new. effort S.

## F14-15: voice speak has no batch form — CONFIRMED (P3, high)
- Re-verified: voice server has 4 tools, speak only (servers/voice/server.py:80); speak→speak bigram = 80 EXACT.
  speak ×190 census-verified. Additive speak_lines with per-line degrade matches the never-raise contract
  (clean-verified #16). dup: new. effort S.

## F14-16: session_recap unbounded — CONFIRMED, reproduced live (P2, high)
- Re-verified at HEAD (recap.py moved to ~53-70 after #763's wrapper-line filter — which added NO size bound): _beat()
  returns entry.text verbatim; only count (12) bounded.
- REPRODUCED at HEAD: 12 × 4KB entries → format_recap = 48,631B. Auditor's 34,657B production measurement is consistent.
- Spec (per-entry ~400-char sentence-boundary cap + ~6KB budget, defaulted params, short-entry byte-identical
  regression) — invariant-clean. dup: new (distinct from #749's CONTENT decontamination, which landed in #763). effort S.

## F14-17: scene_context lean tail uncapped — CONFIRMED (P2, med)
- Re-verified at HEAD: _scene_recent_narration (now ~9034-9063) returns `{"text": e.text}` verbatim, count-bounded only;
  the #763 diff touched exactly this function and added only the heartbeat filter — no cap. The DEFAULT-OFF posture
  (story is the north star; this is the lean DM's story memory) is the correct story-first spec; wrapper-tunable + duo
  A/B on story_craft before changing defaults. dup: new. effort S.

## F14-18: voice/rules not alwaysLoad — ToolSearch hops to discover speak — CONFIRMED (P2, high)
- Re-verified: play.sh:115-122 — engine alwaysLoad: True; clawdnd-rules and clawdnd-voice configured WITHOUT it;
  run_duo.sh sets alwaysLoad only on clawdnd-engine (103-109). speak ×190 through discovery hops; pair with F14-6's trim
  so the +~9KB is paid for. dup: new (enriches #753). effort S.

## F14-19: cross-server lookup_item name collision — CONFIRMED (P3, high)
- Re-verified: rules/server.py:481 vs engine/server.py:5820, both `lookup_item`, different semantics (SRD text vs
  catalog feeding add_item). No-rename + bidirectional docstring cross-link + docstring-assertion test is the only
  wire-contract-safe fix. dup: new. effort S.

## F14-20: id-alias tolerance on 9 used tools — CORRECTED: fold into F14-8 (P3, med)
- Facts verified: skill_check has skill-NAME aliases but no character-id param aliases; award_xp/saving_throw/
  spell_save_dc/remove_combatant et al. take bare character_id; attack carries the coalescing prologue.
- CORRECTION: the only failure mode ever OBSERVED on these tools is wrong VALUE (slug-for-id) — which F14-8's _char
  resolver fixes for all 9 at once, since they all route through _char. The residual (param-NAME aliases) has zero hits
  in 345 files — prophylactic. Not a standalone backlog item: ship as a rider on F14-8's PR (same parametrized alias
  test), or drop if F14-8's resolver lands first and the error class stays at zero.

---

## CLEAN-VERIFIED (spot-checked at HEAD; compressed)
1. No traceback leakage in hard errors (FastMCP message-only) — error samples re-read, concise.
2. #716 alias class present on the 17 listed tools (spot-checked log_event/remember/forget/travel_to/cast_spell/
   load_canon_character signatures); skill_check 0 errors in 235 calls (census).
3. social_check companion-target FIXED at HEAD (~6504: companion is a valid target; read-vs-influence + met-flag
   discipline verified in source).
4. update_character retier/recompute + DEX→initiative (#733) present (read in source, 2620-2652).
5. persist_beat adoption healthy; advance delegation sequential (no nested lock) — verified in source.
6. travel_to write path one-lock-one-save with worldsim/backlog/strategic/expiry/wander seams — verified.
7. attack() guard suite present (incapacitated/turn-ownership/reaction mirrored in cast_spell 5311-5337).
8. Core re-grounds lean (census: get_state p50 946B, look_around p50 2,113B).
9. store.py atomic temp+os.replace + version stamping — verified (108-128).
10. scene_context lock discipline + durable-first — verified (sizing is F14-17).
11. Player facade: accepted from v2 (out of skeptic budget; no contrary evidence).
12. Rules server implementation clean; defect is adoption (F14-10, now P2-with-domain-check).
13. lookup_item/find_items did-you-mean is the in-repo model (5825-5831, 1971).
14. cast_spell graceful degradation on un-modeled SRD spells (5283-5290: curated→srd fallback; error only when BOTH miss).
15. No intra-server tool-name duplicates (141 engine tools re-counted live via list_tools).
16. Voice speak never breaks the story loop (degrade-to-text) — consistent with never-raise contract.
17. Sweep archives: zero tool-contract-class bugs — accepted from v2.
18. 88-dead-tail ≠ deletions — treat as trim/pin data. Side-flag for the rests unit stands: ZERO long_rest/short_rest
    calls in 345 files (re-verified in census) — narrated rests skip resource refresh; mech-relevant, out of unit scope.

## CORRECTIONS LEDGER (v2 → v3)
- F14-2: P1→P2 (observed wall class is stale-checkout-era; harness re-root + HEAD top-level net absorb it); spec drops
  the sub-model tolerant-net option (violates intentional _StrictModel strictness).
- F14-3: evidence correction — observed failures carried events=0 (no orphan/dup demonstrated); memories/decision
  already fail-atomic; non-atomic window is events-only. Severity unchanged (P1).
- F14-6: spec feasibility correction — docstring trim alone lands ~145KB, not ≤110KB; deferring the dead tail is the
  load-bearing half of the target.
- F14-10: P1→P2 — impact unproven by the finding's own admission; domain-check-first.
- F14-12: docstrings do disclose REPLACE; defect re-framed as missing affordance + receipt; add #617/#610 coordination.
- F14-20: folded into F14-8 as a rider (value-side tolerance is F14-8; name-side is prophylactic, zero hits).
- Minor: gate-duo2 Bash-grep count is 6 in the dm transcript (auditor said 9); immaterial to F14-1.
