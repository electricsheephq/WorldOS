# Companion Approval Write-Path (D0)

Date: 2026-06-15
Status: PROPOSED — awaiting owner sign-off (D0 decision record; no code in this PR)
Decision class: load-bearing MCP-tool contract + net-new persisted state model
Related work: #593 (epic — relationship & choice memory change-log), #613 (append {delta,reason} at the ~4 mutator sites), #837 (The Table — companion agendas), audit F6-2 (`docs/audits/ENGINE-AUDIT-2026-06-11.md:1170-1191`)

---

## Context

The companion **approval gauge** — `Character.attitude_value` (`servers/engine/models.py:798`,
`int = 0`, scale −100..+100, 0 = neutral) — is the load-bearing input to the approval half of the
living-world arc engine:

- `ArcGate` unlocks when `gate.threshold <= attitude_value` (`models.py:219-241`, evaluated in
  `companion_arc.py:370`) — personal-quest reveals, romance beats, deepened loyalty.
- `CompanionAgenda` `"attitude_below"` fires when `attitude_value < value` (`models.py:251`) — the
  saboteur's betrayal, the zealot's defection.
- `_camp_arc_summary` already reports `points_away` to the next locked gate
  (`server.py:8767-8782`).

**The gauge has no organic mutation path.** Verified empirically against HEAD `ce43437` by running
the real engine: across 12 `record_decision` player-choice calls the gauge stayed frozen at 0. The
only three writers of `attitude_value` in the entire engine are DM/skill-initiated, never wired to a
player choice:

| Writer | Location | Trigger |
| --- | --- | --- |
| `set_attitude` | `server.py:8186` | DM sets an absolute value |
| `adjust_attitude` | `server.py:8212` | DM nudges by `delta` |
| `social_check` (influence) | `server.py:8401` | ±15/−10 on a DC-gated parley |

`record_decision` (`server.py:10223-10259`) — the canonical "a player chose X" tool, mandated by
`SKILL.md` step 4/7 at every real choice — writes `c.decisions` + flags only and **never touches the
gauge**, despite its own docstring selling a choice→consequence callback path. The `Decision` model
(`models.py:1207-1219`) has no approval axis at all (`summary/options/chosen/rationale/actor_ids`).

Consequence: with the gauge pinned at 0, every positive-threshold `ArcGate` never unlocks and
`attitude_below` betrayals never fire under ordinary play. The richly-authored `approval_likes` /
`approval_dislikes` dossier fields (`models.py:475-476`) are **dead data** — read in exactly one place
(`companion.py:301-306`) and only echoed into `companion_advise` for the DM's human judgment, never a
write trigger (the deliberate "engine reads the gauge; the DM judges the cause" F6-2 division of
labor, `companion.py:258-260`).

We need to add the **first player-choice → gauge writer**, and decide the **shape** of that
write-path: which tool carries it, what the caller supplies, how the engine computes the delta, and
where the provenance of each change lands. This is a load-bearing decision because the gauge is read
by gate contracts that must never see a fiction-derived value, the carrier tool's signature is bound
by 13 test callers + 9 skill-prompt mentions, and the persistence target is a net-new strict-model
field that old snapshots must round-trip.

### Invariants this decision must hold

1. **Engine-sole-writer.** Only engine code, under `campaign_lock`, via the single
   `_clamp_attitude(old + delta)` idiom (`server.py:8158`/`8212`), may mutate `attitude_value`. Gates
   read only engine-mutated values.
2. **Additive-safety.** Existing tool callers and old snapshots must be byte-identical-unaffected. New
   params keyword-defaulted; new persisted fields `Optional`/list-default.
3. **Gauge-backed-not-fiction.** The cause of a delta must be a STRUCTURED, caller-supplied signal
   (a number, or a closed engine-owned enum). The engine must NEVER NLP-infer a delta by matching
   free-text fiction (a decision `summary`) against free-text dossier strings (`approval_likes`).
   Audit F6-2 rejects dossier-tag NLP matching by name (`ENGINE-AUDIT-2026-06-11.md:1187`).

---

## Options considered

### Option A — additive keyword-only structured-cause param on `record_decision`/`resolve_event`
Append `approval: Optional[list] = None` carrying `[{companion_id, delta|cause_tag, reason}]`. The
engine clamps via `_clamp_attitude` under the existing lock and appends a change-log entry.

- **For:** Rides the carrier the DM's hand is already on. At the regard-moving moments
  (spare-the-prisoner, lie-to-the-Harper, ruthless-pragmatism) the DM is already mid-`record_decision`
  call (SKILL step 4/7) — the delta is ~0 marginal reach. Wire-safe: every `record_decision` param
  except `campaign_id` is keyword-defaulted, so a trailing param breaks none of the 13 test callers /
  9 skill-prompt mentions / the lone positional `record_decision(cid, "...", ["comply","resist"])`
  call (`test_content.py:1236`). Old snapshots round-trip (`attitude_value` already defaults 0).
  Audit F6-2 blesses exactly this shape as "Invariant-clean. KEEP."
- **Against:** As one-line-scoped it names only `record_decision` + `resolve_event` and **omits
  `persist_beat`** — the most-reached-for carrier (18 calls vs `record_decision`'s 14), which builds
  its OWN `Decision` at `server.py:11233` and does not call `record_decision`. Missing it re-creates
  the adoption trap one level up. "Engine maps `cause_tag→delta`" needs a taxonomy that does not
  exist, and is invariant-radioactive if `cause_tag` is matched against free-text dossier strings.
  The change-log it implies is unbuilt and ADR-gated (#593).

### Option B — a NEW dedicated `record_approval` / `approval_event` MCP tool
A clean, separate contract: `record_approval(campaign_id, approvals: [{companion_id, delta, reason}])`.

- **For:** Maximal wire safety — touches neither `record_decision` nor `resolve_event`, so zero risk
  to the 13 + 7 existing callers and the alias matrix. Crisp single responsibility; doesn't overload a
  party-scoped decision recorder with a per-companion concern; the cleaner home to grow a richer
  approval surface later.
- **Against:** **Lands at zero adoption — measured, not theorized.** `adjust_attitude` and
  `set_attitude` are exactly this (standalone, clean-contract, lock-guarded, clamped gauge-movers) and
  were invoked **0 and 0 times** across 45 QA transcripts (audit re-census: 0 calls ever, all 20
  snapshot companions at 0). A standalone tool is a SECOND call the beat loop never cues and SKILL
  step 7 actively tells the DM to collapse into one `persist_beat`. It inherits `adjust_attitude`'s
  2-parenthetical-mention discoverability, not `record_decision`'s 17-mention mandated-step
  discoverability. A clean contract for a tool nobody calls leaves the gates inert — it gives the bug
  a nicer API.

### Option C — engine AUTO-TICK: engine matches the action against `approval_likes`/`approval_dislikes` and moves the gauge itself
The gauge moves as a pure side effect of the `record_decision`/`resolve_event` call — zero DM burden,
the literal BG3 "[Companion] approves" moment.

- **For:** Best-possible adoption (zero reach cost — no new param, no delta to author) and it finally
  gives `approval_likes`/`approval_dislikes` a job.
- **Against:** **Invariant-radioactive as specified.** `Decision` carries no structured tag
  (`models.py:1207-1219`, all free text) and `approval_likes`/`dislikes` are free-text content strings.
  So "match the action against the dossier" can ONLY mean free-text-to-free-text (NLP) matching — the
  engine inferring from fiction whether "spared the prisoner" counts as a "mercy" like — feeding a
  value the gates read. That is the exact path invariant 3, the `companion.py:297` contract ("approval
  causes go in the RETURN for the DM to judge — never auto-applied"), and audit F6-2 explicitly forbid.
  The only invariant-clean form of C requires FIRST adding a structured tag to `Decision` AND making
  the DM author it on every call — at which point C has become "A with a tag cause," and it is strictly
  worse on adoption (the DM must supply a tag matching a per-companion vocabulary it often won't have in
  context; a missing/mismatched tag silently re-freezes the gauge).

---

## Decision

**Adopt Option A, in its corrected three-site, structured-cause form — a HYBRID of A and the safe
sliver of C.** Concretely:

1. Add one additive keyword-only param, `approval: Optional[list] = None`, to **three** carriers:
   `record_decision`, `resolve_event`, AND `persist_beat`'s `decision` sub-dict (the
   `server.py:11233` `Decision` build) — closing the top-carrier gap the one-line A scope leaves open.
2. Each entry is `{companion_id: str, cause_tag?: str, delta?: int, reason: str}`. The engine resolves
   the numeric delta from a **CLOSED, engine-owned `cause_tag` → points table** (the safe sliver of C:
   a deterministic set-membership/lookup, the same class of operation as `EventTrigger` `flag_set`
   evaluation — NOT NLP over fiction). A raw `delta` is accepted for off-taxonomy nudges; if both are
   given, `cause_tag` wins (and is the encouraged path). At least one of `cause_tag`/`delta` is
   required per entry.
3. The engine writes `attitude_value` under `campaign_lock` via the existing
   `_clamp_attitude(old + delta)` idiom — one clamp path, engine remains sole writer.
4. The engine appends an auditable, additive, append-only **approval change-log** entry recording
   `{companion_id, old, new, delta, cause_tag, reason, decision_id, day, t}` so the viewer/QA can show
   WHY the gauge moved.
5. Pair with the **F6-2.3 adoption lever**: surface per-beat `points_away`-to-next-gate (already
   computed by `_camp_arc_summary`, `server.py:8782`) in `scene_context`/`companion_advise` so the DM
   is cued WHEN regard should move. (Tracked separately; named here because the write-path is
   under-adopted without it.)

The DM supplies the CAUSE (a tag, e.g. `mercy_shown` / `ally_betrayed`), never a bare number it has to
remember and calibrate (the `adjust_attitude` failure mode), and never free-text the engine has to
judge (the Option C failure mode). The engine owns the number and the clamp. Fiction never reaches the
gauge; the DM is never stuck with a naked numeric dial.

---

## Rationale (grounded in the research numbers)

**Adoption is the binding constraint, and only A beats the measured floor.** This bug IS a never-called
write path, so a write-path the cold DM never reaches is functionally identical to the bug. The repo
already ran the Option-B experiment: standalone gauge-movers `adjust_attitude` + `set_attitude` =
**0 + 0 calls across 45 transcripts** (audit re-census: 0 ever; all 20 snapshot companions at 0). The
beat-loop-steered tools were reached for constantly — `record_decision` 14, `persist_beat` 18,
`companion_advise` 25. The cold-DM reach test puts a piggybacked param at 4-5/5 (it's the SAME call the
DM is already making at moments 1/2/4) versus a standalone tool at 0-1/5. B optimizes wire-purity, the
variable that doesn't bind, while regressing reach, the one that does.

**Wire-safety is real and verified at HEAD `ce43437`, not asserted.**
`record_decision` (`server.py:10223-10232`) has every param keyword-defaulted except `campaign_id`;
appending `approval: Optional[list] = None` after `decision=""` breaks **none** of the 13 test callers,
including the positional `record_decision(cid, "...", ["comply","resist"])` at `test_content.py:1236`
(passes only `summary`+`options`), nor the 9 skill-prompt mentions (prose, signature-agnostic), nor the
alias matrix in `test_tool_arg_aliases.py`. `resolve_event` (`server.py:10322`) takes 3
required-positional args and its 7 test callers (`test_event_parley_layer3.py`) pass exactly 3, so a new
param there MUST be keyword-defaulted and appended after `option_label` — additive-keyword holds.
`persist_beat`'s `decision` is already a `dict` (`server.py:11225`), so an `approval` key on it is purely
additive. Old snapshots round-trip because `attitude_value` already defaults 0 and `approval`/the
change-log default `[]` — byte-identical to today.

**The invariant story is clean by construction.** Engine-sole-writer holds: the write reuses
`_clamp_attitude(old + delta)` (`server.py:8212`) under `campaign_lock` — the single existing clamp
idiom, no second write path minted; gates keep reading only engine-mutated `attitude_value`. The
gauge-backed-not-fiction invariant holds because `cause_tag` is matched against a **closed engine-owned
enum table**, never against the free-text `approval_likes`/`approval_dislikes` dossier strings the audit
rejects by name (`ENGINE-AUDIT-2026-06-11.md:1187`). This is the line F6-2 draws — and the fix-spec
(`:1184-1188`) reaches the identical verdict ("optional clamped `approval:[{companion_id,delta,reason}]`
on `record_decision`/`resolve_event`, engine writes under the same lock — sole-writer holds…
Invariant-clean. KEEP"). Adopting A therefore **enriches** #593/#613/#837 rather than conflicting.

**Why the structured `cause_tag` hybrid over the audit's bare `delta`.** The audit fix-spec carries a
raw caller-supplied `delta` + free-text `reason`. That is invariant-safe but reintroduces the
`adjust_attitude` ergonomic failure: a naked number the DM must calibrate per companion. Promoting the
cause to a closed engine enum (the safe, non-auto-tick sliver of Option C) keeps the engine as the
authority on the number — consistent regard math across the campaign, the DM authors intent
(`mercy_shown`) not arithmetic — while staying a deterministic lookup, NOT fiction inference. The `delta`
escape hatch is retained for off-taxonomy cases so the taxonomy need not be exhaustive on day one.

---

## Counter-arguments considered + rebuttals

- **"B is the safest — it touches no existing signature."** True and real, but cheap to match: A's
  trailing keyword-defaulted param is also wire-safe (verified above) WITHOUT paying B's fatal
  adoption cost. Wire-purity is not the objective function here; reach is. *Rebutted.*

- **"A as scoped misses `persist_beat`, the 18-call top carrier."** Correct, and this is the decisive
  correction the debate surfaced — `persist_beat` builds its own `Decision` at `server.py:11233` and
  does NOT call `record_decision`, so a two-site A ships a param the most-adopted path can't reach,
  re-creating the trap. **Accepted and folded into the decision: wire THREE sites, not two.**

- **"`cause_tag → delta` mapping is unspecified and dangerous."** Correct as a warning. Resolved by
  constraining `cause_tag` to a CLOSED engine-owned enum with a fixed points table (D1 sketch below) and
  forbidding any match against free-text dossier fields. If the taxonomy can't classify a moment, the DM
  passes a raw `delta`. *Rebutted by specification.*

- **"C gives the free BG3 popup with zero DM burden."** Only its unsafe free-text form does, and that
  form violates invariant 3 (no structured tag exists on `Decision`; `approval_likes` are free text, so
  the match is necessarily NLP-on-fiction feeding the gates). The safe form of C collapses into "A with a
  tag," with worse adoption. *Rebutted.*

- **"A per-companion concern doesn't belong on a party-scoped `record_decision`."** Legitimate
  conceptual-purity point (the ~5% reservation). Outweighed by the measured adoption floor: a clean
  separate tool is provably never reached. The param is a per-entry list keyed by `companion_id`, so the
  party-scoped decision and the per-companion deltas stay structurally distinct within one call. *Rebutted
  on cost-benefit.*

---

## Risks accepted

1. **Adoption still depends on the cue.** With the param in hand but no trigger, the DM may forget it.
   Accepted because the carrier is already-adopted (reach ~0) and the F6-2.3 gate-distance surfacing is
   the named pairing. If post-ship transcripts show the param under-used, the cue is the lever, not a
   tool redesign.

2. **`cause_tag` taxonomy will be incomplete at launch.** Accepted: the raw-`delta` escape hatch covers
   off-taxonomy moments, and the enum is engine-owned so it can grow additively without a contract break.

3. **`resolve_event` has no `companion_id` binding in its `Outcome`** (`models.py:360-395` has no
   companion axis). Accepted: the approval entry's `companion_id` comes from the caller-supplied
   `approval` list, never inferred from the `Outcome`. The `resolve_event` leg is low-value for adoption
   (0 production callers, test-only) and is wired for completeness/symmetry.

4. **Slight surface growth on three tool signatures + one new model.** Accepted: all additive, all
   default-empty, all round-tripping.

---

## Open questions deferred

1. **Where the change-log entries persist — the ONE genuinely load-bearing, ADR-gated piece.** No
   `attitude_log`/`reputation_log`/`approval_log`/`change_log` token exists anywhere in `models.py` or
   `server.py` (verified this pass). #593 explicitly flags the append-only change-log schema as needing
   a first-principles ADR BEFORE code (Wave 3). `Decision` is a `_StrictModel` (`extra="forbid"`,
   `models.py:90-94`) so the approval entry CANNOT be smuggled onto it — it needs a real net-new field.
   The D1 sketch below proposes the field shape, but the canonical home (per-NPC on `Character` vs a
   campaign-level ledger vs reusing/extending the memory ledger that already indexes `Decision`s) is the
   schema decision #593 owns. **The param + clamp can ship additively; the provenance trail it feeds is
   blocked on that schema decision.** Do not ship the delta-writer without resolving where `{delta,reason}`
   lands, or we regress from "frozen-but-explainable" to "moves-with-no-recorded-why."

2. **The full `cause_tag` taxonomy and its point values** (starter set sketched in D1; the complete table
   + balance pass is D1/D2 content work).

3. **Whether `social_check`'s existing ±15/−10 should migrate onto the same change-log** for a unified
   provenance trail (out of scope here; a #613 mutator-site question).

---

## Reversibility

**High.** Every piece is additive and default-empty:

- The `approval` param defaults `None`; omitting it is byte-identical to today's behavior. Removing the
  param later is a clean revert (no caller passes it until DM-skill prompts are updated).
- The change-log field is `Optional`/list-default; old and new snapshots round-trip both directions.
  Reverting drops a field that defaulted empty.
- The `_clamp_attitude` write reuses the existing idiom — no new write path to unwind.
- The decision is a strict superset of "do nothing": if the approach proves wrong, stop emitting the
  param and the gauge returns to its current (frozen) behavior with no migration.

The single irreversible-ish commitment is the **change-log schema** (open question 1) — which is exactly
why it is deferred to the #593 ADR rather than decided here. This D0 commits to the WRITE-PATH SHAPE; it
does not commit to the persistence schema.

---

## D1 implementation sketch

### 1. `cause_tag` taxonomy → delta mapping (closed engine-owned table)

A module-level constant in the engine (e.g. `APPROVAL_CAUSE_DELTAS` in `server.py` or a small
`approval.py`), a fixed `dict[str, int]`. Starter set (final values are a D1/D2 balance pass):

```python
APPROVAL_CAUSE_DELTAS: dict[str, int] = {
    # positive
    "mercy_shown":        +5,
    "promise_kept":       +5,
    "ally_defended":      +8,
    "selfless_sacrifice": +10,
    "value_honored":      +5,   # acted in line with a companion's `values`
    # negative
    "cruelty":            -5,
    "promise_broken":     -5,
    "ally_betrayed":      -10,
    "value_violated":     -5,
    "greed_over_people":  -8,
}
```

Resolution (deterministic, no fiction inference):

```python
def _resolve_approval_delta(entry: dict) -> int:
    tag = entry.get("cause_tag")
    if tag:
        if tag not in APPROVAL_CAUSE_DELTAS:        # closed enum — unknown tag is an error, not a guess
            raise ValueError(f"unknown approval cause_tag {tag!r}; known: {sorted(APPROVAL_CAUSE_DELTAS)}")
        return APPROVAL_CAUSE_DELTAS[tag]           # tag wins if both given
    if "delta" in entry and entry["delta"] is not None:
        return int(entry["delta"])                  # off-taxonomy escape hatch
    raise ValueError("approval entry needs a cause_tag (preferred) or a numeric delta")
```

Note: `value_honored`/`value_violated` are deliberately NOT auto-derived by matching the decision
`summary` against the dossier `values`/`approval_likes` — the DM (who is judging the cause anyway, per
the F6-2 division of labor) supplies the tag explicitly. The engine never reads the free-text dossier to
pick a tag. That is the bright line keeping fiction out of the gauge.

### 2. Change-log field shape (additive; final home is the #593 ADR — see open question 1)

A net-new append-only model, default-empty, round-tripping old snapshots. Illustrative shape (the
canonical attachment point — per-`Character` vs campaign-ledger — is the deferred schema decision):

```python
class ApprovalLogEntry(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("approval"))
    t: float = Field(default_factory=_now)
    day: int = 1
    companion_id: str
    old: int                 # attitude_value before
    new: int                 # attitude_value after (post-clamp)
    delta: int               # new - old (post-clamp; may differ from requested if clamped)
    cause_tag: str = ""      # the engine-enum tag, or "" when a raw delta was used
    reason: str = ""         # free-text provenance for the viewer/QA ("spared the prisoner Astarion wanted dead")
    decision_id: str = ""    # links back to the Decision that carried this delta

# Attached additively, e.g.:
#   class Character(...):
#       approval_log: list[ApprovalLogEntry] = Field(default_factory=list)
# default_factory=list => old snapshots (no key) round-trip; [] is byte-identical to today.
```

### 3. Exact param changes + byte-identical-for-existing-callers guarantee

**`record_decision`** (`server.py:10223`) — append after `decision=""`:

```python
def record_decision(
    campaign_id: str,
    summary: str = "",
    options: Optional[list] = None,
    chosen: str = "",
    rationale: str = "",
    actor_ids: Optional[list] = None,
    sets_flag: str = "",
    decision: str = "",
    approval: Optional[list] = None,      # NEW: [{companion_id, cause_tag?|delta?, reason}]
) -> dict:
```
Every existing caller is `campaign_id`-positional + keyword (or the single positional `summary,options`
call at `test_content.py:1236`); a trailing defaulted param is invisible to all of them.

**`resolve_event`** (`server.py:10322`) — its 3 params are all required-positional, so the new param MUST
be keyword-defaulted and appended LAST:

```python
def resolve_event(
    campaign_id: str,
    event_id: str,
    option_label: str,
    approval: Optional[list] = None,      # NEW; the 7 test callers pass exactly 3 positional args
) -> dict:
```

**`persist_beat`** (`server.py:11142`) — NO signature change; the `decision` sub-dict grows an optional
`approval` key, consumed where the inner `Decision` is built (`server.py:11233`):

```python
# decision = {"summary": ..., "chosen": ..., "approval": [{companion_id, cause_tag?|delta?, reason}]}
approval_entries = list(decision.get("approval") or [])   # additive; absent key => []
```

**Shared apply path** (engine-sole-writer, one clamp, under the lock already held by all three tools):

```python
def _apply_approval(c, approval_entries, decision_id=""):
    applied = []
    for entry in (approval_entries or []):
        cid = entry["companion_id"]
        ch = _char(c, cid)
        delta = _resolve_approval_delta(entry)
        old = ch.attitude_value
        ch.attitude_value = _clamp_attitude(ch.attitude_value + delta)   # the ONE clamp idiom
        ch.approval_log.append(ApprovalLogEntry(                          # provenance — see open Q1 for final home
            day=c.day, companion_id=cid, old=old, new=ch.attitude_value,
            delta=ch.attitude_value - old, cause_tag=entry.get("cause_tag", ""),
            reason=entry.get("reason", ""), decision_id=decision_id,
        ))
        applied.append({"companion_id": cid, "old": old, "new": ch.attitude_value})
    return applied
```

Each tool calls `_apply_approval(c, approval, decision_id=d.id)` inside its existing `with
campaign_lock(...)` block, before `save_campaign(c)`, and folds `applied` into its return dict. Old
callers pass nothing → `approval_entries` is `[]` → no gauge write, no log entry, return shape gains no
key → **byte-identical to today**.

### 4. Test plan (D1)

- New: tag→delta resolution (known tag, unknown tag raises, raw-delta hatch, both-given precedence).
- New: gauge moves + clamps at ±100 bounds via each of the three carriers; change-log entry recorded
  with correct `old`/`new`/`delta`/`decision_id`.
- New: ArcGate now unlocks and `attitude_below` agenda now fires from a `record_decision` path (the
  end-to-end "frozen gauge thawed" assertion).
- Regression: all 13 `record_decision` + 7 `resolve_event` existing callers and the alias matrix pass
  unchanged; an old snapshot (no `approval_log` key) round-trips.
