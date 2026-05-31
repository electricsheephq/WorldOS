/* Screen: Campaign Table — live session: scene art + party + GM narration + actions */

// #335: hard viewer-side guard against DM-INTERNAL housekeeping leaking into the
// player-facing story scroll. The /chat tail can carry the DM agent "thinking out
// loud" — a GM-Advisory directive ("NPC introduced but hasn't spoken — record their
// first memory with `remember`") or a bare engine-tool mention. That text is the
// AI-DM equivalent of a leaked system prompt and must NEVER render in the chronicle.
// The engine stays the sole writer; this is a read/projection filter only.
//
// `sanitizeNarration(text)` returns a cleaned narration string, or "" when the WHOLE
// beat was internal (caller drops it). It is line-oriented so a single stray advisory
// line inside an otherwise-real beat is removed without nuking the prose around it.
// #347 extends it with a SENTENCE-level pass that strips story-craft scaffolding (dice
// tallies, plot-structure jargon, "beat complete" stage-directions) embedded mid-line —
// see the `_isScaffoldingSentence` block below for the HIGH-CONFIDENCE-only patterns.
const DM_ENGINE_TOOLS = [
  "remember", "recall", "recall_decisions", "log_event", "add_quest",
  "update_decision", "record_decision", "add_consequence", "check_consequences",
  "world_tick", "travel_to", "advance_time", "long_rest", "downtime", "award_xp",
  "adjust_reputation", "social_check", "companion_advise", "check_companion_arc",
  "lookup_lore", "recall_lore", "resolve_scene_debt", "end_combat", "start_combat",
  "end_session", "begin_session", "create_character", "level_up", "set_scene",
];
// A line whose ENTIRE content is a GM-advisory directive or a bare tool reference.
const _TOOLS_ALT = DM_ENGINE_TOOLS.join("|");
// Header line of the right-panel Director advisory if it ever bleeds into prose.
const _GM_ADVISORY_HEADER = /^\s*(?:#{1,6}\s*)?(?:\**\s*)?GM\s+Advisory\b/i;
const _ADVISORY_SUBTITLE = /^\s*what the campaign owes the story\b/i;
// The scene-debt KIND labels (mirrors servers/engine/director.py debt kinds; the GM-Advisory
// panel renders them with underscores→spaces, e.g. "npc_introduced_silent" → "npc introduced
// silent"). #357 (nb3): the WHOLE advisory panel string leaked to the player —
// "npc introduced silent NPC 'Vanos' has been introduced but hasn't spoken — give them a line
// or record their first memory with remember." A line that LEADS with one of these debt-kind
// labels is GM bookkeeping, never fiction.
// HIGH-CONFIDENCE only: the space-rendered form is limited to "npc introduced silent" (the
// nb3 leak; never fiction) — the other kinds' nudge BODIES are already caught below, and their
// space-forms ("quest stalled", "due consequence") could brush legitimate prose. The raw
// underscore tokens never occur in prose, so all of those are safe to list verbatim.
const _DEBT_KIND_LABEL =
  "(?:npc introduced silent|hook_untracked|npc_introduced_silent|quest_stalled|" +
  "choice_without_outcome|due_consequence|thread_pressure)";
// The debt-nudge family (mirrors servers/engine/director.py::_nudge) — DM-facing
// imperatives that name an engine tool / structural-debt action.
const _ADVISORY_DIRECTIVE = new RegExp(
  "(?:" +
    // #357: a line LED by a scene-debt kind label (optionally back-ticked) — the panel leak.
    "^\\s*[`'\"]?\\s*" + _DEBT_KIND_LABEL + "\\b|" +
    "\\b(?:has been introduced but hasn'?t spoken)\\b|" +
    "\\b(?:untracked hook)\\b.*\\bcall\\b|" +
    "\\bquest\\b.*\\bhas stalled\\b|" +
    "\\bwas offered but never resolved\\b|" +
    "\\bconsequence\\b.*\\b(?:is due|overdue)\\b|" +
    "\\bstanding thread\\b.*\\b(?:overdue|world-?beat)\\b|" +
    "\\b(?:record|give) (?:their|them) (?:a line|first memory)\\b|" +
    // generic "…with/via/using/call <tool>" imperative naming an engine tool
    "\\b(?:call|use|via|with|using)\\b[^.]{0,40}\\b(?:" + _TOOLS_ALT + ")\\b" +
  ")", "i",
);
// A line that is ESSENTIALLY just an engine-tool token (optionally back-ticked,
// optionally with a trivial call signature) — e.g. "`remember`", "remember(...)".
const _BARE_TOOL_LINE = new RegExp(
  "^\\s*[`'\"(]*\\s*(?:" + _TOOLS_ALT + ")\\s*(?:\\([^)]*\\))?\\s*[`'\")]*\\s*[.;:]?\\s*$",
  "i",
);
function _isInternalLine(line) {
  const t = (line || "").trim();
  if (!t) return false; // keep blank lines for caller's join (they're harmless)
  return _GM_ADVISORY_HEADER.test(t)
    || _ADVISORY_SUBTITLE.test(t)
    || _ADVISORY_DIRECTIVE.test(t)
    || _BARE_TOOL_LINE.test(t);
}

// #347: the SECOND class of DM-internal leak — the DM agent's STORY-CRAFT scaffolding
// (its private act/beat plan, dice tallies, and "beat complete" stage-directions)
// bleeding into player prose. Unlike the #335 advisory/tool leaks (which arrive as their
// OWN line), scaffolding tends to land as a trailing CLAUSE/SENTENCE inside an otherwise
// real narration line ("Zevlor held silence after three failed social checks; … the spine
// hook. Meeting beat of the cold open complete."). So this guard works at two grains:
//   (a) a dice/check TALLY is excised as a sub-clause IN PLACE — "Zevlor held silence after
//       three failed social checks" → "Zevlor held silence." — so the real prose survives;
//   (b) plot-craft JARGON + "beat complete" STAGE-DIRECTIONS drop the whole sentence (these
//       sentences are scaffolding through-and-through), keeping the prose around them.
//
// CRITICAL: HIGH-CONFIDENCE phrasings ONLY — never bare common words. A war-drum's "beat",
// a tavern "scene", or "the second act of the play" are legitimate fiction and MUST survive.
// "beat"/"scene"/"act" alone never trigger; they only do inside a fixed scaffolding frame.

// Spelled-out + digit small numbers, for "three failed social checks" / "3 missed saves".
const _NUM_WORD = "(?:\\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)";
// (1) Dice / CHECK tallies: a count + a result word + a check/roll/save noun. Anchored on
// the check/roll/save noun so "three failed assaults" / "two broken oaths" (real prose) DON'T match.
// `_TALLY` (global) is used for in-place clause excision; it eats a leading connective
// preposition ("after"/"following"/"with"/…) when present so no dangling "after ." is left.
const _TALLY = new RegExp(
  "\\s*(?:after|following|despite|past|on|with)?\\s*\\b" + _NUM_WORD +
  "\\s+(?:failed|successful|missed|botched|passed|blown)\\s+" +
  "(?:\\w+\\s+){0,2}(?:checks?|rolls?|saves?|saving throws?)\\b",
  "gi",
);
// A non-global twin for boolean detection (a global regex carries lastIndex state).
const _TALLY_TEST = new RegExp(_TALLY.source, "i");
// (2) Unambiguous plot-CRAFT terms — multi-word jargon that never occurs in fiction prose.
const _CRAFT_JARGON = /\b(?:spine[- ]hook|inciting incident|midpoint reversal)\b/i;
// (3) Stage-directions / status summaries — a meta line stamping the scene's structural state.
//   - "<word> beat complete", "beat complete", "cold open complete", "Act 2 complete/wraps"
//   - "meeting beat" / "<word> beat of the cold open" (the verbatim #347 form)
//   - "connects/connecting … to the spine hook", "this is the <setup|payoff|midpoint> beat"
//   - "X of the Y complete" where Y is a structural word (beat/act/scene/cold open)
const _STAGE_DIRECTION = new RegExp(
  "(?:" +
    // a structural unit being marked done/wrapped (optional copula: "cold open IS complete")
    "\\b(?:cold open|(?:\\w+\\s+)?beat|act(?:\\s+(?:one|two|three|\\d+))?|scene\\s+\\d+|sequence)\\s+" +
      "(?:is\\s+|was\\s+|now\\s+)?(?:complete|completed|done|over|wraps?|wrapped|resolved)\\b|" +
    // "meeting/arrival/threshold beat" — naming a beat by its craft role
    "\\b(?:meeting|arrival|threshold|inciting|setup|payoff|climax|midpoint|opening|closing)\\s+beat\\b|" +
    // explicitly wiring the moment to the arc machinery
    "\\bconnect(?:s|ing)?\\b[^.]{0,30}\\b(?:spine[- ]hook|the spine|main arc)\\b|" +
    // labeling THIS moment as a named structural beat
    "\\bthis (?:is|completes|closes) the\\b[^.]{0,30}\\b(?:setup|payoff|midpoint|reversal|beat|act|cold open)\\b|" +
    // "<anything> of the <structural-unit> complete" (e.g. "Meeting beat of the cold open complete")
    "\\bof the\\b[^.]{0,30}\\b(?:cold open|beat|act|scene)\\b[^.]{0,30}\\bcomplete\\b" +
  ")", "i",
);
// True when a WHOLE sentence is plot-craft jargon or a stage-direction (drop the sentence).
// The tally is handled separately (in-place excision), so it's NOT a whole-sentence-drop trigger.
function _isScaffoldingSentence(sentence) {
  const s = (sentence || "").trim();
  if (!s) return false;
  return _CRAFT_JARGON.test(s) || _STAGE_DIRECTION.test(s);
}
function _hasScaffolding(text) {
  return _TALLY_TEST.test(text) || _CRAFT_JARGON.test(text) || _STAGE_DIRECTION.test(text);
}
// Clean scaffolding from one line, preserving the real prose. Two grains:
//   1. excise dice/check TALLY phrases in place (keeps the rest of their sentence);
//   2. split into sentences (on . ! ? ;, terminator kept) and DROP any sentence that is
//      wholly plot-craft jargon / a stage-direction.
// Returns the cleaned line (possibly "").
function _stripScaffoldingSentences(line) {
  if (typeof line !== "string" || !line) return line;
  // Fast path: nothing scaffolding-shaped here, don't touch the line at all.
  if (!_hasScaffolding(line)) return line;
  // (1) In-place tally excision (global; reset lastIndex defensively).
  _TALLY.lastIndex = 0;
  let cleaned = line.replace(_TALLY, "");
  // (2) Whole-sentence drop for jargon / stage-directions.
  const parts = cleaned.match(/[^.!?;]+[.!?;]+|[^.!?;]+$/g);
  if (parts) cleaned = parts.filter((p) => !_isScaffoldingSentence(p)).join("");
  // Tidy punctuation/space the excisions may have left (" ." / "  " / leading "; " /
  // a clause separator now dangling at the end, e.g. "Zevlor held silence;" → "…silence.").
  return cleaned
    .replace(/\s+([.!?;,])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .replace(/^[\s;,.]+/, "")
    .replace(/\s*[;,]\s*$/, ".")
    .trim();
}
function sanitizeNarration(text) {
  if (typeof text !== "string" || !text) return "";
  const kept = text
    .split(/\r?\n/)
    // #347: first excise any scaffolding sentences embedded in a line…
    .map((line) => _stripScaffoldingSentences(line))
    // …then drop any line that is wholly a #335 advisory/tool-name internal line (or was
    // emptied by the scaffolding strip above — _isInternalLine returns false on "", so an
    // emptied line survives as "" and is harmlessly collapsed by the blank-run join below).
    .filter((line) => !_isInternalLine(line));
  // Collapse the blank-line runs an excised directive may leave behind.
  return kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

// #337: the quick-action buttons (Continue / Say / Do / Check / Save) and the dice buttons are
// icon+label only — a first-timer can't tell how they differ from typing free-text + Declare, so
// the #324 newbie ignored all of them. These short hints surface as native `title=` tooltips
// (hover + most screen readers expose them) so the affordance is discoverable with zero new DOM.
// Keyed by the engine action `id` (server.py build_action_model); viewer-only copy, no wire change.
const ACTION_HINTS = {
  continue: "Continue — advance the scene without adding a new action of your own.",
  say: "Say — speak in-character; formats your input as spoken dialogue.",
  do: "Do — attempt a physical action; formats your input as something your hero does.",
  check: "Check — roll a skill or ability check (Perception, Persuasion, …).",
  save: "Save — make a saving throw to resist or avoid an effect.",
  attack: "Attack — strike a foe; uses your action this turn.",
  "bonus-action": "Bonus — take a bonus action this turn (a quick second move).",
  reaction: "Reaction — respond out of turn (e.g. an opportunity attack or parry).",
};
const DICE_HINT = (sides) => `Roll a d${sides} — ask the Dungeon Master to resolve a d${sides} check.`;
const DECLARE_HINT = "Type what your hero does in your own words, then Declare to take the turn.";

// #402: the maximum number of chronicle rows MOUNTED at once (the live tail + the leading history
// band, merged). Keeps the DOM + the accessibility tree bounded so the newest DM beat and the
// action bar stay reachable no matter how long the session runs. Generous on purpose: well above a
// handful of turns AND above one multi-paragraph DM turn, so a beat is never clipped as it streams.
// Older beats are still available in full in the Quest Journal.
const CHRONICLE_RENDER_CAP = 50;

// #405: assemble the chronicle's full ordered, de-duplicated row list from its three sources. Pure
// (no React, no DOM) so the exactly-once + chronological-order contract is unit-testable. The whole
// narration-duplication fix lives here + in app.jsx's useLiveSession dedup:
//   • ORDER by the STABLE session-log sequence (`orderSeq` on a live /events beat / `seq` on a
//     recentEvents row — both = the engine's absolute session-log line index) when present, so the
//     live tail can never interleave out of chronological order; fall back to the client-side ingest
//     counter `.at` for rows with no seq (player echoes, a chat-only beat). Array.prototype.sort is
//     stable (ES2019+), so equal-key rows keep insertion order.
//   • DEDUP recentEvents (the server's trailing window of the SAME session log the live /events
//     stream reads) against the live tail so a paragraph never shows in BOTH bands. Prefer the
//     stable `seq` (a row in both bands shares it → the match is immune to the prose AND to a
//     windowing re-mount); fall back to a normalized TEXT key only for rows lacking a seq (legacy
//     server / a chat-only beat), keyed identically to app.jsx's text fallback. Non-narration
//     history rows (rolls/system/combat) are always kept.
// recentEvents stay the leading (oldest) band: they are the session log's trailing lines, all at or
// before the live tail's lines, and the dedup guarantees no overlap — so a plain concat is in order.
function buildChronicleLog(recentEvents, chatBeats, log) {
  const recent = Array.isArray(recentEvents) ? recentEvents : [];
  const beats = Array.isArray(chatBeats) ? chatBeats : [];
  const echoes = Array.isArray(log) ? log : [];
  const sanitize = (t) => (typeof window !== "undefined" && typeof window.sanitizeNarration === "function")
    ? window.sanitizeNarration(t || "") : (t || "");
  const narrationKey = (t) => sanitize(t || "").replace(/\s+/g, " ").trim().toLowerCase();
  const orderOf = (e) => (e && typeof e.orderSeq === "number") ? e.orderSeq : null;
  const mergedTail = [...beats, ...echoes].sort((a, b) => {
    const sa = orderOf(a), sb = orderOf(b);
    if (sa !== null && sb !== null) return sa - sb;   // both from the session log → true beat order
    return (a?.at || 0) - (b?.at || 0);               // else fall back to client ingest order
  });
  const liveSeqs = new Set(
    mergedTail.filter((b) => b && b.kind === "narration" && typeof b.orderSeq === "number").map((b) => b.orderSeq),
  );
  const liveNarrationKeys = new Set(
    mergedTail.filter((b) => b && b.kind === "narration").map((b) => narrationKey(b.text)).filter(Boolean),
  );
  const dedupedRecent = recent.filter((row) => {
    const kind = (row && (row.kind || row.type)) || "narration";
    if (kind !== "narration" && kind !== "dialogue") return true;  // mechanics rows always kept
    const seq = row && row.seq;
    if (typeof seq === "number") return !liveSeqs.has(seq);  // stable-id match (prose-independent)
    const key = narrationKey(row && (row.text || row.detail));
    return !key || !liveNarrationKeys.has(key);
  });
  return [...dedupedRecent, ...mergedTail];
}
// Exposed for tests/devtools introspection (additive — the component calls the local fn directly).
if (typeof window !== "undefined") window.buildChronicleLog = buildChronicleLog;

function ScreenTable({ onNavigate, state, setState, liveSession }) {
  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const activeCampaign =
    campaigns.find((c) => c.id === state?.activeCampaign) ||
    campaigns[0] ||
    {};
  const campaignId = activeCampaign.campaign_id || state?.activeCampaign || activeCampaign.id || "";
  const [surface, setSurface] = React.useState(null);
  const [surfaceStatus, setSurfaceStatus] = React.useState("loading");
  const demoLog = [];
  const [input, setInput] = React.useState("");
  // #340: the in-flight-turn state (the /chat tail + accumulated beats, the local player echo, and
  // the "DM is narrating…" pending indicator) is owned by the APP (useLiveSession) so it survives
  // screen navigation — a DM beat that lands while the player is on another screen still gets
  // ingested, and the narrating state clears on the turn that actually resolved it. ScreenTable
  // reads/writes that lifted state through the `liveSession` prop. A no-op fallback keeps the
  // screen renderable in isolation (e.g. a direct deep-link before the hook has bound a campaign).
  const session = liveSession || { chatBeats: [], log: [], pending: null, armPending: () => {}, clearPending: () => {}, recordPlayerEcho: () => {} };
  const { chatBeats, log, pending } = session;
  const logRef = React.useRef(null);
  const inputRef = React.useRef(null);
  // #402: auto-follow state. `stickToBottomRef` is true while the player is at/near the bottom of the
  // chronicle (the default) and false once they scroll UP to read history — so the auto-scroll effect
  // follows new narration to the bottom WITHOUT yanking a reader back down mid-read. `snapNextRef` is
  // a one-shot "force to bottom on the next content change" flag set when the player submits a move
  // (a new turn) — so declaring an action always re-pins to the latest, even if they'd scrolled up.
  const stickToBottomRef = React.useRef(true);
  const snapNextRef = React.useRef(false);
  const toast = window.useToast ? window.useToast() : (() => {});
  const fallbackParty = [];
  const party = Array.isArray(surface?.party) && surface.party.length ? surface.party : fallbackParty;
  const quests = Array.isArray(surface?.activeQuests) ? surface.activeQuests : [];
  const stash = Array.isArray(surface?.quickInventory) ? surface.quickInventory : [];
  const conditions = Array.isArray(surface?.conditions) ? surface.conditions : [];
  const recentEvents = Array.isArray(surface?.recentEvents) ? surface.recentEvents : [];
  const actions = Array.isArray(surface?.availableActions) ? surface.availableActions : [];
  const enabledActions = Array.isArray(surface?.enabledActions) ? surface.enabledActions : actions.filter((a) => a?.available);
  const blockedActions = Array.isArray(surface?.blockedActions) ? surface.blockedActions : actions.filter((a) => !a?.available);
  // #G3: split the action model by group so the MAIN-column palette can show exploration verbs
  // (say/do/check/continue/cast/use) always, and combat verbs (attack/bonus/reaction) only when a
  // fight is on. No truncation — every verb the read model emits renders (the old right-rail
  // slice(0,6) silently dropped bonus-action + reaction). `actionsInCombat` keys off the same
  // engine-mutated combat gauge the read model uses (any combat verb is enabled, or the encounter
  // is flagged active) — never off fiction.
  const explorationActions = actions.filter((a) => a.group !== "combat");
  const combatActions = actions.filter((a) => a.group === "combat");
  const actionsInCombat = Boolean(surface?.encounter?.active) || combatActions.some((a) => a.available);
  const writeLane = surface?.writeLane || { endpoint: surface?.write_lane || "/move" };
  const actionContext = surface?.actionContext || {};
  const consequenceContext = actionContext?.consequences || {};
  const roundOrder = Array.isArray(surface?.roundOrder) ? surface.roundOrder : [];
  const scene = surface?.scene || {};
  const encounter = surface?.encounter || {};
  const calendar = surface?.calendar?.available ? surface.calendar : null;
  const calendarMoon = Array.isArray(calendar?.moons) ? calendar.moons[0] : null;
  const calendarDetail = calendar ? [calendar.season, calendarMoon ? `${calendarMoon.name}: ${calendarMoon.phase}` : ""].filter(Boolean).join(" · ") : "";
  const [activeHero, setActiveHero] = React.useState(() => party[0]?.id || "");
  const hero = party.find((p) => p.id === activeHero) || party[0] || { id: "", name: "Hero", short: "Hero", level: 1, class: "Adventurer", hp: 1, hpMax: 1 };
  const visibleQuests = quests.filter((q) => !q.status || q.status === "active" || q.status === "open");
  const canAct = Boolean(surface?.can_act);
  const readOnlyReason = blockedActions.find((a) => a.disabled_reason)?.disabled_reason || "read-only surface";
  // #274/#393/#405: the chronicle merges three sources — recentEvents (the server's trailing window
  // of the session log), chatBeats (the live DM/player tail), and log (local optimistic player
  // echoes) — into one ordered, de-duplicated list. The merge/dedup/order is a PURE function
  // (buildChronicleLog, below) so the exactly-once + chronological-order contract is unit-testable
  // without mounting the component. See its doc-comment for the stable-`seq`-keyed reconciliation.
  const visibleLog = surface ? buildChronicleLog(recentEvents, chatBeats, log) : [...demoLog, ...log];
  // #402: BOUND what the chronicle RENDERS. Even with the live tail capped in useLiveSession, the
  // leading history band (recentEvents from the server) can be large, so the merged list could still
  // mount hundreds of rows into the DOM + the accessibility tree — the exact thing that buried the
  // latest DM beat (and the action box) and made an a11y reader truncate before the newest content.
  // We render only the most-recent CHRONICLE_RENDER_CAP rows so a 10-beat session is as navigable as
  // a 2-beat one: the latest beat is always near the bottom of a short, fully-exposed list, and the
  // sticky action bar below is always reachable. Older beats remain in the Quest Journal (full
  // history); a one-line affordance says so when rows are hidden. The cap is generous (≫ a handful of
  // turns, and ≫ one multi-paragraph DM turn) so we never clip an in-flight beat as it streams.
  const hiddenLogCount = Math.max(0, visibleLog.length - CHRONICLE_RENDER_CAP);
  const renderedLog = hiddenLogCount > 0 ? visibleLog.slice(visibleLog.length - CHRONICLE_RENDER_CAP) : visibleLog;
  const actionById = (id) => actions.find((a) => a.id === id);
  const enabledActionById = (id) => enabledActions.find((a) => a.id === id);

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    const params = new URLSearchParams();
    if (campaignId) params.set("campaign", campaignId);
    if (activeCampaign.source) params.set("source", activeCampaign.source);
    if (activeCampaign.runId) params.set("run", activeCampaign.runId);
    const query = params.toString() ? `?${params.toString()}` : "";
    try {
      const response = await fetch(`/session-surface${query}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`session surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      setSurface(payload);
      setSurfaceStatus("ready");
    } catch (error) {
      if (isCancelled()) return;
      setSurfaceStatus(error?.message || "unavailable");
    }
    // #357 (nb3): the GM Advisory (Campaign Director #72) fetch was removed here — its only
    // consumer was the GM-bookkeeping panel that leaked into the player's live-play sidebar
    // (see the RIGHT column below). The director advisory still loads on the journal surface.
    // NOTE (#340): the live DM-narration /chat tail used to be polled HERE, but it's now owned by
    // the app-level useLiveSession hook (app.jsx) so a beat that lands while the player is on
    // another screen still gets ingested and the narrating indicator clears correctly. ScreenTable
    // only loads its own surface; the chronicle's chat beats arrive via the `liveSession`
    // prop. (Engine stays sole writer — this is purely where the read-poll lives.)
  }, [campaignId, activeCampaign.source, activeCampaign.runId]);

  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    const guardedLoad = async () => {
      if (cancelled) return;
      await loadSurface(() => cancelled);
    };
    const stopPolling = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const startPolling = () => {
      if (timer === null) {
        timer = window.setInterval(guardedLoad, 5000);
      }
    };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        guardedLoad();
        startPolling();
      } else {
        stopPolling();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    handleVisibility();
    return () => {
      cancelled = true;
      stopPolling();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [loadSurface]);

  React.useEffect(() => {
    if (!party.some((p) => p.id === activeHero)) {
      setActiveHero(party[0]?.id || "");
    }
  }, [party, activeHero]);

  // #402: auto-follow the newest narration to the bottom — but RESPECT a reader who scrolled up.
  // The old effect pinned scrollTop to scrollHeight on EVERY content change unconditionally, which
  // (a) yanked a player back down the instant a streamed paragraph or 5s surface poll arrived while
  // they were reading history, and (b) never fired when ONLY the pending/narrating indicator toggled
  // (it wasn't a dependency), so the "DM is narrating…" beat could sit below the fold. Now we scroll
  // to bottom only when the player is already at/near the bottom (stickToBottomRef) OR a new move was
  // just submitted (snapNextRef, a one-shot re-pin on a new turn). Depending on `pending` too means
  // the narrating indicator (and a freshly-streamed beat) is followed into view the same way.
  React.useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    if (snapNextRef.current || stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
      snapNextRef.current = false;
      stickToBottomRef.current = true;  // a programmatic snap leaves us pinned to the bottom
    }
  }, [renderedLog, pending]);

  // #402: track whether the player is reading history (scrolled up) vs. parked at the bottom. A
  // generous threshold (~64px) keeps "near the bottom" sticky through small layout shifts (the
  // narrating dots, a wrapping line) so normal play stays auto-following; deliberately scrolling up
  // to re-read clears it, and scrolling back to the bottom re-arms it.
  const onLogScroll = React.useCallback(() => {
    const el = logRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distanceFromBottom <= 64;
  }, []);

  // #340 + #342: arming / clearing the "DM is narrating…" pending state now lives in the app-level
  // useLiveSession hook (so it survives navigation, and carries the 90s recovery + 12-min backstop).
  // ScreenTable just calls into it. `pendingActive` is the gate for the action bar — a turn that the
  // recovery timeout flagged `stuck` is NO LONGER pending (the bar re-opens so the player can retry).
  const armPending = session.armPending;
  const recordPlayerEcho = session.recordPlayerEcho;
  const pendingActive = Boolean(pending && !pending.stuck);
  const pendingStuck = Boolean(pending && pending.stuck);
  // #385: the COLD-OPEN (first beat) gets action-bar copy that reads as "the DM is taking its turn"
  // (alive) rather than the generic "Narrating…" — so the locked bar matches the obviously-alive
  // chronicle indicator and never looks like the app is wedged.
  const pendingFirstBeat = Boolean(pendingActive && pending.firstBeat);
  // #344: remember the move that is currently in flight so the "Try again" recovery (shown when a
  // turn goes `stuck`) can actually RE-POST it. The first submit clears the input box (setInput("")),
  // so by the time the bar re-opens stuck the box is empty — without the original move stored, the
  // old "Try again" path (sendAction → input.trim() → empty → early-return) was a silent no-op. We
  // keep the canonical move object + its label here, set on every postMove, so a retry resends the
  // exact stalled turn (free-text, roll, or structured action alike).
  const lastMoveRef = React.useRef(null);

  // #342: surface the recovery exactly once when a turn goes `stuck` so the player knows the bar
  // re-opened on purpose (the DM stalled) rather than silently.
  const stuckNotified = React.useRef(false);
  React.useEffect(() => {
    if (pendingStuck && !stuckNotified.current) {
      stuckNotified.current = true;
      toast({ kind: "danger", title: "The Dungeon Master seems stuck", body: "No reply came back in time — your input is re-enabled. Try again or rephrase." });
    }
    if (!pending) stuckNotified.current = false;
  }, [pendingStuck, pending, toast]);

  const postMove = async (move, label, actionId) => {
    const enabledAction = actionId ? enabledActionById(actionId) : null;
    if (!move || !canAct || pendingActive || (actionId && !enabledAction)) {
      toast({ kind: "danger", title: "Action unavailable", body: pendingActive ? "The Dungeon Master is still narrating — one move at a time." : readOnlyReason });
      return;
    }
    // #342: neutralize any markup in a free-text move (kind "do"/"say"/etc. carry the player's words
    // in move.text) BEFORE it is sent to the engine OR echoed — so an injection-y turn can't choke
    // the DM or ride along in the chronicle as raw markup. Structured moves (no free text) pass through.
    const cleanMove = (typeof move.text === "string" && move.text)
      ? { ...move, text: window.neutralizeMarkup(move.text) }
      : move;
    const rawLabel = label || cleanMove.text || cleanMove.name || "declares an action";
    const text = window.neutralizeMarkup(String(rawLabel)) || "declares an action";
    // #344: capture the (already-neutralized) move + label + actionId so a later "Try again"
    // recovery can re-POST this exact turn if the DM stalls on it.
    lastMoveRef.current = { move: cleanMove, label: text, actionId };
    try {
      const response = await fetch(writeLane.endpoint || "/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...cleanMove, campaign: surface?.campaign_id || campaignId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.reason || `move ${response.status}`);
      }
      recordPlayerEcho(hero.name, text);
      armPending(text);
      // #402: a new turn was just submitted — force the chronicle back to the bottom on the next
      // content change even if the player had scrolled up, so they always see their move land and
      // the DM's reply begin. The auto-follow effect honors this one-shot, then re-arms stickiness.
      snapNextRef.current = true;
      loadSurface();
    } catch (error) {
      toast({ kind: "danger", title: "Move not sent", body: error?.message || `The viewer could not reach ${writeLane.endpoint || "/move"}.` });
    }
  };

  const sendAction = async () => {
    if (pendingActive) return;
    const text = input.trim();
    if (!text) return;
    const action = actionById("do");
    if (!action?.available) {
      toast({ kind: "danger", title: "Declare is unavailable", body: action?.disabled_reason || readOnlyReason });
      return;
    }
    await postMove({ kind: "do", text }, text, "do");
    setInput("");
  };

  // #344: the recovery action for a `stuck` turn. The old "Try again" reused sendAction, which reads
  // the (now-empty) input box and early-returned — a dead button. This handler resends the stalled
  // turn: if the player typed something new into the re-opened box we send THAT (a rephrase, the
  // toast's other option); otherwise we re-POST the exact move that stalled. Either way the bar
  // re-arms via postMove → armPending, so the turn goes back to "narrating" and the recovery clears.
  const retryStuck = async () => {
    const typed = input.trim();
    if (typed) {
      // Player rephrased — send the new text as a fresh `do` (this path already worked).
      await sendAction();
      return;
    }
    const last = lastMoveRef.current;
    if (!last || !last.move) {
      // Nothing captured to retry (shouldn't happen for a stuck turn) — nudge the player to type.
      toast({ kind: "danger", title: "Nothing to retry", body: "Type your action again, then press Try again." });
      inputRef.current?.focus();
      return;
    }
    // Re-POST the stalled move verbatim. postMove re-arms pending + the recovery/backstop timers.
    await postMove(last.move, last.label, last.actionId);
  };

  // #344: the Declare button doubles as the stuck-recovery "Try again" button (same slot, relabeled).
  // Route the click to the right handler so a stuck turn actually retries instead of no-op'ing.
  const onDeclareClick = () => (pendingStuck ? retryStuck() : sendAction());

  const requestRoll = (sides = 20) => {
    const action = actionById("check");
    if (!action?.available) {
      toast({ kind: "danger", title: `d${sides} unavailable`, body: action?.disabled_reason || readOnlyReason });
      return;
    }
    postMove({ kind: "check", name: `d${sides}`, text: `roll d${sides}` }, `requests a d${sides} roll`, "check");
  };

  const invokeAction = (action) => {
    if (pendingActive) {
      toast({ kind: "danger", title: "Action unavailable", body: "The Dungeon Master is still narrating — one move at a time." });
      return;
    }
    if (!action?.available) {
      toast({ kind: "danger", title: action?.label || "Action unavailable", body: action?.disabled_reason || readOnlyReason });
      return;
    }
    if (action.ui) {
      inputRef.current?.focus();
      return;
    }
    if (action.move) {
      postMove(action.move, action.label, action.id);
    }
  };

  return (
    <div className="screen" id="worldos-screen-table" data-worldos-testid="openworlds-root" style={{ height: "100%", display: "grid", gridTemplateColumns: "260px 1fr 280px", gap: 14, padding: 14 }}>

      {/* LEFT — Party roster */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
        <Panel framed style={{ padding: "14px 18px", flex: "0 0 auto" }}>{/* #320: tighter panel padding */}
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>{encounter.active ? encounter.summary : "Session"}</div>
          <SectionTitle>The Party</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {party.length ? party.map((p) => (
              <PartyRow
                key={p.id}
                p={p}
                active={activeHero === p.id}
                onClick={() => setActiveHero(p.id)}
              />
            )) : <div className="body-sm muted">No party</div>}
          </div>
        </Panel>

        <Panel framed style={{ padding: "14px 18px", flex: "1 1 auto", minHeight: 0, overflow: "auto" }}>{/* #320: tighter panel padding */}
          <SectionTitle>Conditions</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {conditions.length ? conditions.map((c) => (
              <ConditionRow key={c.id || `${c.name}:${c.who}`} icon={c.icon || "◆"} name={c.name} who={c.who} detail={c.detail} tone={c.tone} />
            )) : <div className="body-sm muted">No active party conditions.</div>}
          </div>
        </Panel>
      </div>

      {/* CENTER — Scene + log */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
        {surfaceStatus !== "ready" && (
          <div
            role={surfaceStatus === "loading" ? "status" : "alert"}
            aria-live="polite"
            data-worldos-testid="app-status-banner"
            data-worldos-status={surfaceStatus}
            className="body-sm"
            style={{
              padding: "8px 12px",
              color: surfaceStatus === "loading" ? "var(--ink-700)" : "var(--crimson)",
              background: "rgba(176,141,87,0.08)",
              boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
            }}
          >
            {surfaceStatus === "loading" ? "Loading session surface." : `Session surface unavailable: ${surfaceStatus}`}
          </div>
        )}
        {/* Scene plate */}
        <div style={{ position: "relative", flex: "0 0 auto" }}>
          <Img
            scope={scene.imageScope || ""}
            label={`scene · ${scene.caption || surface?.location?.name || activeCampaign.title || "Open Worlds"}`}
            h={260}
            framed
            style={{ width: "100%" }}
          />
          {/* Glow + caption */}
          <div className="candleglow" style={{ width: 200, height: 200, left: "30%", top: "30%" }} />
          {/* Readability scrim — keeps the caption legible over any scene art (#242 G) */}
          <div style={{
            position: "absolute", left: 0, right: 0, bottom: 0, height: "62%",
            background: "linear-gradient(180deg, rgba(20,12,4,0) 0%, rgba(20,12,4,0.55) 55%, rgba(20,12,4,0.9) 100%)",
            pointerEvents: "none",
          }} />
          <div style={{
            position: "absolute", bottom: 14, left: 14, right: 14,
            display: "flex", justifyContent: "space-between", alignItems: "flex-end",
            pointerEvents: "none",
          }}>
            <div style={{ minWidth: 0, maxWidth: "min(620px, 62%)" }}>
              <Pill tone="royal" dot>{surface?.dayLabel || activeCampaign.day || "Unknown time"}</Pill>
              {calendarDetail && <div className="body-xs" title={calendarDetail} style={{ marginTop: 4, color: "var(--p-150)", textShadow: "0 1px 2px rgba(0,0,0,0.75)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{calendarDetail}</div>}
              <div className="hand" style={{
                marginTop: 6, color: "#f4ecd8", fontSize: 16, lineHeight: 1.45,
                textShadow: "0 1px 3px rgba(0,0,0,0.95), 0 0 12px rgba(0,0,0,0.6)",
                display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden",
              }}>
                {scene.summary || activeCampaign.recap || "The table is waiting for a campaign snapshot."}
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, pointerEvents: "auto" }}>
              <BrassButton tone="dark" size="sm" onClick={() => onNavigate("map")}>Travel</BrassButton>
              <BrassButton tone="dark" size="sm" onClick={() => onNavigate("dialogue")}>Parley</BrassButton>
              <BrassButton tone="dark" size="sm" onClick={() => onNavigate("map", { openCamp: true })}>Camp</BrassButton>
            </div>
          </div>
        </div>

        {/* Log */}
        <Panel framed style={{ flex: "1 1 auto", display: "flex", flexDirection: "column", minHeight: 0, padding: "14px 18px" }}>{/* #320: tighter panel padding */}
          {/* #320: trimmed "The Tabletop Chronicle" → "Chronicle" and dropped the "·" lead dot
              (read as visual noise on a busy screen). */}
          <SectionTitle>Chronicle</SectionTitle>
          {/* #402: role="log" + a label names this region in the accessibility tree so an assistive
              reader can target "the latest beat" directly; `onScroll` tracks whether the player is
              reading history (scrolled up) so the auto-follow effect doesn't yank them to the bottom
              mid-read. The scroll region is the SOLE grower (flex 1 1 auto) — the action bar below is
              flex 0 0 auto, so it stays anchored/visible no matter how long the chronicle gets. */}
          <div
            ref={logRef}
            tabIndex={0}
            role="log"
            aria-label="Chronicle — most recent narration at the bottom"
            data-worldos-testid="narration-log"
            onScroll={onLogScroll}
            style={{ flex: "1 1 auto", overflow: "auto", paddingRight: 12 }}
          >
            {/* #402: when older beats are windowed out of the DOM, say so + point to the full history
                (the Quest Journal). Keeps the rendered list short so the newest beat + action box stay
                reachable, without pretending the earlier story is gone. */}
            {hiddenLogCount > 0 && (
              <div className="body-xs muted" style={{ margin: "0 0 10px", padding: "6px 0", borderBottom: "1px solid rgba(140,100,60,0.2)" }}>
                {hiddenLogCount} earlier {hiddenLogCount === 1 ? "beat is" : "beats are"} kept in your{" "}
                <button className="btn ghost sm" style={{ padding: "0 4px" }} onClick={() => onNavigate("journal")}>Quest Journal</button>
                {" "}— the latest beats are shown below.
              </div>
            )}
            {renderedLog.length ? renderedLog.map((entry, i) => (
              <LogEntry key={entry.id || `${entry.kind || "n"}-${i}`} entry={entry} />
            )) : <div className="body-sm muted">No moves yet</div>}
            {pendingActive && <DmNarratingBeat since={pending.since} firstBeat={pending.firstBeat} />}
            {pendingStuck && <DmStuckBeat />}
          </div>

          {/* #G3: PRIMARY action palette — promoted into the MAIN column, anchored in the Chronicle
              panel footer directly above the Declare box. This is the obvious, unmissable way to act
              in the main play flow (it is NOT buried in the right rail any more). ALL relevant verbs
              render — no slice(0,6) cap: exploration verbs (Say/Do/Check/Continue/Cast/Use) always,
              combat verbs (Attack/Bonus/Reaction) in an "In Combat" group when in combat. Reuses the
              EncounterButton component + invokeAction + ACTION_HINTS — the click path is unchanged.
              flex 0 0 auto so it stays anchored/visible no matter how long the chronicle grows. */}
          <div data-worldos-testid="action-palette" style={{ flex: "0 0 auto", marginTop: 14 }}>
            <SectionTitle>Actions</SectionTitle>
            {!actions.length && (
              <div className="body-sm muted" style={{ marginTop: 4 }}>
                No actions are available until a campaign snapshot loads.
              </div>
            )}
            {explorationActions.length > 0 && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 6, marginTop: 8 }}>
                {explorationActions.map((a) => (
                  <EncounterButton
                    key={`${a.group}:${a.id}`}
                    icon={a.available ? (a.icon || "quest.scroll") : "inventory.locked"}
                    label={a.label}
                    detail={a.available ? a.groupLabel : a.disabled_reason}
                    hint={ACTION_HINTS[a.id]}
                    actionId={a.id}
                    tone={a.available ? "" : "crimson"}
                    disabled={!a.available || pendingActive}
                    onClick={() => invokeAction(a)}
                  />
                ))}
              </div>
            )}
            {actionsInCombat && combatActions.length > 0 && (
              <React.Fragment>
                <div className="body-sm" style={{ color: "var(--crimson)", fontFamily: "var(--f-display)", letterSpacing: "0.12em", textTransform: "uppercase", fontSize: 11, marginTop: 12, marginBottom: 6 }}>
                  In Combat
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 6 }}>
                  {combatActions.map((a) => (
                    <EncounterButton
                      key={`${a.group}:${a.id}`}
                      icon={a.available ? (a.icon || "combat.attack") : "inventory.locked"}
                      label={a.label}
                      detail={a.available ? a.groupLabel : a.disabled_reason}
                      hint={ACTION_HINTS[a.id]}
                      actionId={a.id}
                      tone={a.available ? "royal" : "crimson"}
                      disabled={!a.available || pendingActive}
                      onClick={() => invokeAction(a)}
                    />
                  ))}
                </div>
              </React.Fragment>
            )}
          </div>

          {/* DECLARE: free-text action box — the other primary input, paired with the palette above.
              Action bar — #402: flex 0 0 auto so it is ALWAYS anchored at the bottom of the panel and
              never pushed out of view by an ever-growing chronicle above it. */}
          <div data-worldos-testid="move-composer" style={{ flex: "0 0 auto", marginTop: 14, padding: 12, background: "rgba(80,50,20,0.06)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)" }}>
            <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8 }}>
              <span className="eyebrow">Active</span>
              <strong data-worldos-testid="active-player" data-worldos-actor-id={hero.id || undefined} style={{ fontFamily: "var(--f-display)", color: "var(--ink-900)", letterSpacing: "0.1em" }}>
                {hero.name}
              </strong>
              <div style={{ flex: 1 }} />
              {/* #337: dice buttons explain themselves on hover — a newbie didn't know d20/d12/d8/d6 ask the DM for a check. */}
              <button type="button" data-worldos-testid="dice-button" data-worldos-die="20" aria-label="Roll d20" onClick={() => requestRoll(20)} title={DICE_HINT(20)} className="btn ghost sm" disabled={!actionById("check")?.available || pendingActive}>{window.OpenWorldsIcon?.has?.("dice.d20") && <window.OpenWorldsIcon id="dice.d20" size={13} />} d20</button>
              <button type="button" data-worldos-testid="dice-button" data-worldos-die="12" aria-label="Roll d12" onClick={() => requestRoll(12)} title={DICE_HINT(12)} className="btn ghost sm" disabled={!actionById("check")?.available || pendingActive}>{window.OpenWorldsIcon?.has?.("dice.roll") && <window.OpenWorldsIcon id="dice.roll" size={13} />} d12</button>
              <button type="button" data-worldos-testid="dice-button" data-worldos-die="8" aria-label="Roll d8" onClick={() => requestRoll(8)} title={DICE_HINT(8)} className="btn ghost sm" disabled={!actionById("check")?.available || pendingActive}>{window.OpenWorldsIcon?.has?.("dice.roll") && <window.OpenWorldsIcon id="dice.roll" size={13} />} d8</button>
              <button type="button" data-worldos-testid="dice-button" data-worldos-die="6" aria-label="Roll d6" onClick={() => requestRoll(6)} title={DICE_HINT(6)} className="btn ghost sm" disabled={!actionById("check")?.available || pendingActive}>{window.OpenWorldsIcon?.has?.("dice.roll") && <window.OpenWorldsIcon id="dice.roll" size={13} />} d6</button>
            </div>
            {/* #337: one-line hint under the action bar so a first-timer knows free-text + Declare is the core loop, distinct from the quick-action buttons. */}
            <div className="body-xs muted" style={{ marginBottom: 6 }}>
              Type freely and press <strong>Declare</strong>, or use the <strong>Actions</strong> above (hover each for what it does).
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <input
                ref={inputRef}
                aria-label="Describe your move"
                data-worldos-testid="move-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && onDeclareClick()}
                disabled={pendingActive}
                title={DECLARE_HINT}
                placeholder={pendingFirstBeat ? "The Dungeon Master is composing your opening scene…" : pendingActive ? "The Dungeon Master is narrating…" : pendingStuck ? "The DM seemed stuck — try again." : (canAct ? "Describe what your hero does..." : `Read-only: ${readOnlyReason}`)}
                style={{ ...inkInput, fontFamily: "var(--f-body)", fontSize: 16, opacity: pendingActive ? 0.6 : 1 }}
              />
              <BrassButton onClick={onDeclareClick} title={pendingStuck ? "Re-send your last action to the Dungeon Master, or type a new one first." : DECLARE_HINT} disabled={!actionById("do")?.available || pendingActive} testId="move-submit" ariaLabel={pendingStuck ? "Try action again" : "Declare move"}>{pendingFirstBeat ? "Composing…" : pendingActive ? "Narrating…" : pendingStuck ? "Try again" : "Declare"}</BrassButton>
            </div>
          </div>
        </Panel>
      </div>

      {/* RIGHT — Quests + Quick stash + Encounter (GM Advisory removed #357 — see below) */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
        {/* #357 (nb3): the "GM Advisory" panel (Campaign Director debts — "what the campaign
            OWES the story", with the raw debt-kind label + a tool-naming nudge like "…record
            their first memory with remember") is GM/director bookkeeping, NOT player-facing
            content. It leaked into a newbie's live-play sidebar here on the PLAYER table screen.
            The advisory still renders on the journal/Director surface (screen-journal.jsx) where
            a director framing is appropriate; it is removed from the player's live-play view. */}

        <Panel framed style={{ padding: "14px 18px" }}>{/* #320: tighter panel padding */}
          <SectionTitle>Quests</SectionTitle>{/* #320: "Active Quests" → "Quests" */}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {visibleQuests.map((q) => (
              <button key={q.id} onClick={() => onNavigate("journal")} style={{
                textAlign: "left",
                padding: "10px 12px",
                background: "rgba(176,141,87,0.08)",
                boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
                cursor: "pointer",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.1em", color: "var(--ink-900)" }}>
                    {q.title}
                  </span>
                  <Pill tone={q.tone}>{q.label}</Pill>
                </div>
                <div className="hand" style={{ fontSize: 13, color: "var(--ink-600)", marginTop: 2 }}>{q.objective}</div>
              </button>
            ))}
            {!visibleQuests.length && <div className="body-sm muted">No active quests</div>}
          </div>
        </Panel>

        <Panel framed style={{ padding: "14px 18px" }}>{/* #320: tighter panel padding */}
          <SectionTitle right={<button className="btn ghost sm" onClick={() => onNavigate("inventory")}>Open</button>}>Quick Stash</SectionTitle>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
            {stash.slice(0, 8).map((it) => (
              <IconPlate key={it.id} size={48} label={it.name || it.glyph || "Item"} glyph={it.icon || it.glyph || "inventory.potion"} framed />
            ))}
            {!stash.length && <div className="body-sm muted" style={{ gridColumn: "1 / -1" }}>Inventory empty</div>}
          </div>
          <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between" }}>
            <Stat label="Items" value={stash.length} />
          </div>
        </Panel>

        <Panel framed style={{ padding: "14px 18px", flex: 1, minHeight: 0, overflow: "auto" }}>{/* #320: tighter panel padding */}
          <SectionTitle>Encounter</SectionTitle>
          <div className="body-sm muted" style={{ marginBottom: 10 }}>
            {encounter.summary || scene.summary || "Choose what to risk."}
            {Number(consequenceContext.dueCount || 0) > 0 && (
              <span style={{ color: "var(--crimson)" }}> · {consequenceContext.dueCount} consequence due</span>
            )}
          </div>
          {/* #G3: the actionable palette now lives in the MAIN column beside the Declare
              box (see ActionPalette below SceneHeader's chronicle panel) so it is the
              obvious, primary way to act and is never buried in this side rail. This rail
              keeps only the encounter framing + Round Order. */}
          <div className="body-sm muted" style={{ marginBottom: 4 }}>
            {actions.length
              ? "Your moves are in the main column, beside Declare."
              : "No actions are available until a campaign snapshot loads."}
          </div>

          <div className="divider" style={{ margin: "14px 0" }}>
            <div className="diamond"></div>
          </div>

          <SectionTitle>Round Order</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {roundOrder.length ? roundOrder.map((t, i) => (
              <div key={t.id || t.name || `round-${i}`} style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "6px 10px",
                background: t.active ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
                boxShadow: t.active ? "inset 0 0 0 1px var(--b-500)" : "inset 0 -1px 0 rgba(140,100,60,0.2)",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  {t.active && <span style={{ color: "var(--crimson)", fontFamily: "var(--f-display)", fontSize: 12 }}>▶</span>}
                  <span className="body-sm" style={{ color: t.foe ? "var(--crimson)" : "var(--ink-800)", fontStyle: t.foe ? "italic" : "normal" }}>
                    {t.name}
                  </span>
                </div>
                <span style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-600)" }}>{t.init}</span>
              </div>
            )) : <div className="body-sm muted">No active combat round.</div>}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function PartyRow({ p, active, onClick }) {
  const hp = Number.isFinite(Number(p.hp)) ? Number(p.hp) : 1;
  const hpMax = Number.isFinite(Number(p.hpMax)) && Number(p.hpMax) > 0 ? Number(p.hpMax) : 1;
  const hpRatio = Math.max(0, Math.min(1, hp / hpMax));
  return (
    <button onClick={onClick} style={{
      display: "grid", gridTemplateColumns: "44px 1fr", gap: 10, alignItems: "center",
      padding: "8px",
      background: active ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
      boxShadow: active
        ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
        : "inset 0 0 0 1px rgba(140,100,60,0.25)",
      textAlign: "left",
      cursor: "pointer",
      transition: "all 140ms",
    }}>
      <Img scope={p.id ? "portrait-" + p.id : ""} label={p.short} w={44} h={56} framed />
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
          {p.name}
        </div>
        <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)" }}>
          Lvl {p.level} {p.class}
        </div>
        <div style={{ display: "flex", gap: 4, marginTop: 4, alignItems: "center" }}>
          <div style={{ flex: 1, height: 6, background: "rgba(0,0,0,0.15)", borderRadius: 1, position: "relative", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.4)" }}>
            <div style={{
              position: "absolute", inset: 0, right: `${(1 - hpRatio) * 100}%`,
              background: hpRatio > 0.5 ? "linear-gradient(180deg, #5a8a3a, #3a6020)" : "linear-gradient(180deg, var(--crimson), #4a1010)",
            }} />
          </div>
          <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-700)" }}>{hp}/{hpMax}</span>
        </div>
      </div>
    </button>
  );
}

function ConditionRow({ icon, name, who, detail, tone }) {
  const iconNode = window.OpenWorldsIcon?.has?.(icon)
    ? <window.OpenWorldsIcon id={icon} size={16} label={name} />
    : icon;
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 8, alignItems: "center",
      padding: "6px 10px",
      background: "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.2)",
    }}>
      <span style={{ color: `var(--${tone === "crimson" ? "crimson" : tone === "royal" ? "royal" : "b-500"})`, fontSize: 16 }}>{iconNode}</span>
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {name} <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>· {who}</span>
        </div>
        <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)" }}>{detail}</div>
      </div>
    </div>
  );
}

function LogEntry({ entry }) {
  const kind = entry.kind || "narration";
  if (entry.kind === "narration") {
    // #335: render-path-complete guard. Every narration source (the /chat tail AND
    // engine recentEvents) flows through this one branch, so sanitizing here means a
    // GM-advisory/tool-name leak can't reach the player regardless of which projection
    // produced it. A beat that is wholly internal renders nothing.
    const text = sanitizeNarration(entry.text);
    if (!text) return null;
    return (
      <div style={{ margin: "14px 0", display: "flex", gap: 12 }}>
        <div style={{
          width: 4, alignSelf: "stretch",
          background: "linear-gradient(180deg, var(--b-400), transparent)",
        }} />
        {/* #G4: whiteSpace:"pre-line" honors the DM's blank-line paragraph breaks
            (the sibling skill PR emits \n\n) so a multi-paragraph beat renders as
            separated paragraphs instead of one run-on block. sanitizeNarration is
            still applied above, untouched. */}
        <div className="body" style={{ flex: 1, whiteSpace: "pre-line" }}>
          <span className="eyebrow" style={{ color: "var(--crimson)", marginRight: 8 }}>Chronicle</span>
          {text}
        </div>
      </div>
    );
  }
  if (entry.kind === "action") {
    return (
      <div style={{ margin: "10px 0" }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {entry.who}
        </span>
        <span className="hand" style={{ marginLeft: 8, color: "var(--ink-700)" }}>—</span>
        <span className="body" style={{ marginLeft: 8, color: "var(--ink-800)" }}>{entry.text}</span>
      </div>
    );
  }
  if (entry.kind === "roll") {
    return (
      <div style={{ margin: "8px 0", display: "flex", gap: 10, alignItems: "center" }}>
        <Pill tone="emerald">d{entry.sides ?? 20}</Pill>
        <span style={{ fontFamily: "var(--f-mono)", fontSize: 13, color: "var(--ink-700)" }}>
          {entry.who} {entry.text}
        </span>
      </div>
    );
  }
  if (entry.kind === "dialog") {
    return (
      <div style={{ margin: "10px 0" }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--royal)" }}>
          {entry.who}
        </span>
        <span className="body" style={{ marginLeft: 8, fontStyle: "italic" }}>"{entry.text}"</span>
      </div>
    );
  }
  return (
    <div style={{ margin: "8px 0" }}>
      <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-700)" }}>
        {entry.label || kind}
      </span>
      <span className="body" style={{ marginLeft: 8, color: "var(--ink-800)" }}>{entry.text || entry.detail}</span>
    </div>
  );
}

// #327 + #336: the persistent "DM is narrating…" beat shown in the chronicle while a submitted
// move is being resolved. The DM's turn is long (35–60+s), so this is the difference between "the
// world is thinking" and "the app froze". #328 added the label + pulsing dots; #336 adds the
// missing *progress* signal so a 35–60s wait no longer reads as a freeze:
//   • a LIVE elapsed-time readout (0:07 → 0:42 …) — the strongest "still alive" cue, and it keeps
//     ticking even under reduced-motion (it's information, not decoration);
//   • a one-line "this can take up to a minute" hint so a first-timer knows the wait is expected.
// a11y: role="status" + aria-live="polite" announces the wait ONCE on mount. The per-second
// elapsed counter and the dots are aria-hidden so a screen reader isn't spammed every tick.
// Reduced-motion: the pulsing dots + shimmer are disabled (CSS below + the global token); the
// elapsed text and hint remain, so the "is it busy?" question is still answered without motion.
// #348: `firstBeat` makes the expectation HONEST. The DM beat lands all-at-once (no streaming),
// and the FIRST beat — the cold-open/Act-opening the engine spends minutes building — legitimately
// takes several minutes. Telling a first-timer "up to a minute" then re-opening the bar at 90s was
// the #348 false-stuck trap. For the opening we say "a few minutes"; later beats say "a minute or
// two" (the ~35–60s norm, but a content-rich beat 2–4 runs 90–120s — #399). This copy mirrors the
// adaptive recovery window in app.jsx (later-beat window raised 90s → 180s in #399).
// #385: the rotating "the world is being made" flavor lines for the COLD-OPEN only. The first beat
// legitimately takes minutes (the engine builds the world + sets the scene; no streaming), and the
// old single static line ("Setting the opening scene — …") read as FROZEN: it never changed, the
// pulsing dots / shimmer / elapsed were all decorative-and-aria-hidden, so to the accessibility tree
// AND to a single screenshot the whole affordance collapsed to two unchanging strings. A fresh
// player (and the §8.2 a11y-snapshot harness) saw nothing move and gave up. These lines ROTATE every
// few seconds so consecutive renders/snapshots DIFFER — visible, honest progress (it's the DM
// composing, not fake percent-progress) — and they're announced via the live region below.
const DM_COLD_OPEN_FLAVOR = [
  "The Dungeon Master is composing your opening scene…",
  "Lighting the candles and unrolling the map…",
  "Gathering the threads of your story…",
  "Setting the stage and casting the first players…",
  "Sketching the world around you…",
  "The ink is still drying on your opening…",
];

function DmNarratingBeat({ since, firstBeat }) {
  const start = typeof since === "number" ? since : Date.now();
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const secs = Math.max(0, Math.floor((now - start) / 1000));
  const mm = Math.floor(secs / 60);
  const ss = String(secs % 60).padStart(2, "0");
  const elapsedLabel = `${mm}:${ss}`;
  // #385: the headline reads as an ACTIVE process, not a passive status. The cold-open rotates a
  // flavor line every ~4s (so the text itself visibly changes); later beats keep the steady label.
  const label = firstBeat
    ? DM_COLD_OPEN_FLAVOR[Math.floor(secs / 4) % DM_COLD_OPEN_FLAVOR.length]
    : "The Dungeon Master is narrating";
  const waitHint = firstBeat
    ? "The first beat of a session can take a few minutes — hang tight, your story is on its way."
    // #399: a content-rich beat can run up to ~two minutes (the window is 180s); say "a minute or
    // two" so a 90–120s wait reads as expected, not as the app having stalled.
    : "Weaving the next beat — this can take a minute or two.";
  // #385: a11y model for the cold-open. The frozen-app illusion came from the live region being the
  // ONLY accessible text AND it never changing (the dots/shimmer/elapsed were all aria-hidden). Fix:
  //   • The visible label + elapsed are NO LONGER aria-hidden for the first beat, so they appear in
  //     the accessibility tree / ariaSnapshot AND visibly change between renders — the proof-of-life
  //     the §8.2 snapshot harness and a real screen reader can actually perceive.
  //   • But they live OUTSIDE the aria-live region (the outer div is a plain container here): a
  //     polite region re-announces its whole text on every change, so leaving the per-second elapsed
  //     inside it would spam a screen reader every tick — the exact noise #336 avoided by hiding it.
  //   • A SEPARATE visually-hidden role="status" announces the reassurance ONCE on mount (it never
  //     changes → announced once, not per tick). Later beats keep the original single-region behavior.
  if (firstBeat) {
    return (
      <div style={{ margin: "14px 0", display: "flex", gap: 12, opacity: 0.92 }}>
        <div style={{ width: 4, alignSelf: "stretch", background: "linear-gradient(180deg, var(--crimson), transparent)" }} />
        <div className="body" style={{ flex: 1 }}>
          {/* announced ONCE — stable text, so a polite region doesn't re-fire every second */}
          <span role="status" aria-live="polite" style={{
            position: "absolute", width: 1, height: 1, padding: 0, margin: -1,
            overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0,
          }}>
            The Dungeon Master is composing your opening scene. {waitHint}
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            {/* visible + in the a11y tree (NOT aria-hidden) so the rotating text + elapsed prove life */}
            <span className="dm-narrating-label eyebrow" style={{ color: "var(--crimson)" }}>{label}</span>
            <span className="dm-narrating-dots" aria-hidden="true" style={{ display: "inline-flex", gap: 4 }}>
              {[0, 1, 2].map((i) => (
                <span key={i} style={{
                  width: 6, height: 6, borderRadius: "50%", background: "var(--b-400)",
                  animation: "dmNarratePulse 1200ms ease-in-out infinite", animationDelay: `${i * 200}ms`,
                }} />
              ))}
            </span>
            <span style={{ fontFamily: "var(--f-mono)", fontSize: 12, color: "var(--ink-600)", fontVariantNumeric: "tabular-nums" }}>
              {`composing · ${elapsedLabel}`}
            </span>
          </div>
          <div className="hand muted" style={{ fontSize: 12, marginTop: 4 }}>
            {waitHint}
          </div>
        </div>
      </div>
    );
  }
  return (
    <div role="status" aria-live="polite" style={{ margin: "14px 0", display: "flex", gap: 12, opacity: 0.92 }}>
      <div style={{ width: 4, alignSelf: "stretch", background: "linear-gradient(180deg, var(--crimson), transparent)" }} />
      <div className="body" style={{ flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span className="dm-narrating-label eyebrow" style={{ color: "var(--crimson)" }}>{label}</span>
          <span className="dm-narrating-dots" aria-hidden="true" style={{ display: "inline-flex", gap: 4 }}>
            {[0, 1, 2].map((i) => (
              <span key={i} style={{
                width: 6, height: 6, borderRadius: "50%", background: "var(--b-400)",
                animation: "dmNarratePulse 1200ms ease-in-out infinite", animationDelay: `${i * 200}ms`,
              }} />
            ))}
          </span>
          <span aria-hidden="true" style={{ fontFamily: "var(--f-mono)", fontSize: 12, color: "var(--ink-600)", fontVariantNumeric: "tabular-nums" }}>
            {elapsedLabel}
          </span>
        </div>
        <div className="hand muted" style={{ fontSize: 12, marginTop: 4 }}>
          {waitHint}
        </div>
      </div>
    </div>
  );
}

// Keyframes + reduced-motion fallback, injected once (the OpenWorlds bundle is in-browser Babel,
// so a tiny self-contained <style> keeps this affordance from needing a styles.css round-trip).
// A subtle shimmer on the label adds motion beyond the dots; both are stilled under reduced-motion
// (the global [data-reduced-motion='on'] *  rule covers it, and we belt-and-suspenders it here).
//
// #341: the pulse is OPACITY-ONLY (no `transform: scale`). The old scale animation changed each
// dot's bounding box every frame, which kept the chronicle in perpetual layout motion while the
// "narrating" beat was on screen — that is exactly the kind of never-settles churn an automated /
// assistive consumer's "is this element stable yet?" actionability wait can trip on, contributing
// to nav clicks appearing to "time out" during a long narration. Opacity doesn't affect layout, so
// the page is geometrically still while still reading as "the world is thinking".
(function ensureDmNarrateStyle() {
  if (typeof document === "undefined" || document.getElementById("dm-narrate-style")) return;
  const el = document.createElement("style");
  el.id = "dm-narrate-style";
  el.textContent =
    "@keyframes dmNarratePulse{0%,80%,100%{opacity:0.25}40%{opacity:1}}" +
    "@keyframes dmNarrateShimmer{0%,100%{opacity:0.6}50%{opacity:1}}" +
    ".dm-narrating-label{animation:dmNarrateShimmer 1800ms ease-in-out infinite}" +
    "html[data-reduced-motion='on'] .dm-narrating-dots span,html[data-reduced-motion='on'] .dm-narrating-label{animation:none!important}" +
    "html[data-reduced-motion='on'] .dm-narrating-dots span{opacity:0.7}" +
    "@media (prefers-reduced-motion: reduce){.dm-narrating-dots span,.dm-narrating-label{animation:none!important}.dm-narrating-dots span{opacity:0.7}}";
  document.head.appendChild(el);
})();

// #342: the recovery beat shown when a turn went `stuck` (the DM didn't reply within the 90s
// recovery window). It replaces the live "narrating…" beat, re-enables the bar, and tells the
// player the wait was abandoned on purpose so the session can advance instead of freezing.
function DmStuckBeat() {
  return (
    <div role="status" aria-live="polite" style={{ margin: "14px 0", display: "flex", gap: 12, opacity: 0.95 }}>
      <div style={{ width: 4, alignSelf: "stretch", background: "linear-gradient(180deg, var(--crimson), transparent)" }} />
      <div className="body" style={{ flex: 1 }}>
        <span className="eyebrow" style={{ color: "var(--crimson)" }}>The Dungeon Master seems stuck</span>
        <div className="hand muted" style={{ fontSize: 13, marginTop: 4 }}>
          No reply came back in time. Your input is open again — try your action once more, or rephrase it.
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ScreenTable, PartyRow, ConditionRow, LogEntry, DmNarratingBeat, DmStuckBeat, sanitizeNarration });
// #385: expose the cold-open flavor pool for tests/devtools introspection (purely additive —
// nothing in the running app reads it off window; DmNarratingBeat closes over the const directly).
window.DM_COLD_OPEN_FLAVOR = DM_COLD_OPEN_FLAVOR;

function EncounterButton({ icon, label, detail, tone, onClick, disabled, hint, actionId }) {
  const iconNode = window.OpenWorldsIcon?.has?.(icon)
    ? <window.OpenWorldsIcon id={icon} size={17} label={label} />
    : icon;
  return (
    // #337: `title` surfaces the one-line affordance on hover (and as a screen-reader
    // description) without changing the visible label that remains the accessible name.
    <button
      type="button"
      onClick={onClick}
      title={hint || undefined}
      aria-label={hint || label}
      data-worldos-testid="action-button"
      data-worldos-action-id={actionId || undefined}
      style={{
      display: "grid", gridTemplateColumns: "24px 1fr", gap: 8, alignItems: "center",
      textAlign: "left",
      padding: "8px 10px",
      background: "rgba(176,141,87,0.08)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.62 : 1,
      transition: "all 140ms",
    }}
    disabled={disabled}
    onMouseEnter={(e) => {
      if (disabled) return;
      e.currentTarget.style.background = "linear-gradient(180deg, var(--p-100), var(--p-200))";
      e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--b-500), 0 0 16px -6px var(--gold-glow)";
    }}
    onMouseLeave={(e) => {
      if (disabled) return;
      e.currentTarget.style.background = "rgba(176,141,87,0.08)";
      e.currentTarget.style.boxShadow = "inset 0 0 0 1px rgba(140,100,60,0.3)";
    }}>
      <span style={{ color: tone === "crimson" ? "var(--crimson)" : tone === "royal" ? "var(--royal)" : "var(--b-500)", fontSize: 16 }}>{iconNode}</span>
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {label}
        </div>
        <div className="hand muted" style={{ fontSize: 11 }}>{detail}</div>
      </div>
    </button>
  );
}

window.EncounterButton = EncounterButton;
