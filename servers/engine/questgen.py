"""S7 — the quest-generation layer: assemble lore-derived quest SEEDS (hooks) + a guaranteed
4-beat cold-open PRELUDE that the DM weaves. Pure module (no MCP, no campaign I/O), mirroring
``worldsim.py`` / ``content._resolve_quest_variants``: it mutates a ``Campaign`` in place from the
already-SEEDED world (roster, factions, locations, world_state, resolved quest_outcomes), using a
seeded ``random.Random``. Degrade-not-abort — a malformed source is skipped, never raises.

DESIGN (decided post-adversarial — see /tmp/decision-quest-generation.md):
The engine assembles STRUCTURE the DM weaves; it does NOT own quest win-conditions or "monitors".
An adversarial review proved that layer hollow: ``world_state.facts`` is written once at seed and
never mutated in play, so a monitor would watch a constant — only the LLM judges fiction. So a hook
is DATA: a dramatic SHAPE *tag* bound to typed lore nouns + a ``grievance`` (a wrong the lore already
contains), with ``prereq``/``arc_back`` LABELS the DM reads. The DM narrates/advances + sets status;
the engine never evaluates a quest predicate. No 9x20 grammar, no recursion, no pacing director —
all ceremony a frontier LLM ignores.

Anti-blandness lever (cheap, high payoff): APOPHENIA — when binding nouns, prefer entities that
already share a relationship with the grievance (keyword/locale overlap), so connected nouns read as
authored intent. Setting-agnostic: every noun is drawn from the campaign's own entities; the only
authored tables are generic frames (arrival/meeting/shape hints) carrying no setting names.
"""

from __future__ import annotations

import random
import re

from models import Campaign, PreludeBeat, QuestHook

# Generic, setting-agnostic frames (no setting names — these compose with bound nouns).
_ARRIVAL_FRAMES = [
    "a newcomer just off the road, papers thin and purse thinner",
    "a survivor of an off-screen disaster, still ash-streaked and owed nothing",
    "a local on an ordinary errand the moment the day shatters",
    "drawn in by a debt, a letter, or a name owed one last visit",
]
_MEETING_STAKES = [
    "a common threat at a checkpoint forces you back-to-back",
    "a stranger's quiet warning spares you a fatal mistake — and asks one back",
    "you both reach for the same thing in the same breath",
    "a scuffle neither of you started puts you on the same side of it",
]
_SHAPES = ["fetch_plus", "investigation", "hunt", "rescue", "heist", "escort", "faction_war", "dilemma"]
# A shape's fitting NPC motivation (the "why"), from Doran/Parberry's 9 — used as a label only.
_MOTIVATION_BY_SHAPE = {
    "fetch_plus": "equipment", "investigation": "knowledge", "hunt": "protection",
    "rescue": "serenity", "heist": "wealth", "escort": "protection",
    "faction_war": "conquest", "dilemma": "reputation",
}
# Light keyword heuristics → a dramatic shape (else a seeded pick). Bounded, not a grammar.
_SHAPE_HINTS = [
    ("rescue", ("missing", "captive", "captured", "taken", "hostage", "rescue", "freed", "enslaved", "shackled")),
    ("investigation", ("murder", "who", "secret", "rune", "cipher", "unmasked", "informant", "asking why", "schematic")),
    ("heist", ("smuggle", "steal", "stolen", "vault", "control-core", "relic", "looted", "buy back")),
    ("faction_war", ("faction", "rule", "throne", "archduke", "regime", "resistance", "patrol", "occupation", "collabor")),
    ("hunt", ("hunt", "kill", "beast", "broke", "broken", "warband", "crusade", "jailers")),
    ("escort", ("escort", "guard", "convoy", "refugee", "smuggling out", "get them out")),
]

_TOKEN = re.compile(r"[a-z][a-z'\-]{3,}")  # words of length >= 4, lowercased
_STOP = {
    "the", "and", "that", "with", "from", "into", "have", "this", "they", "them", "their",
    "what", "when", "where", "will", "your", "you're", "over", "under", "been", "more",
    "some", "than", "then", "there", "these", "those", "which", "while", "about", "after",
    "before", "still", "every", "someone", "anyone", "no-one", "nobody", "city", "lower",
}


def _toks(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(str(text).lower()) if t not in _STOP}


def _pick_shape(grievance: str, note: str, rng: random.Random) -> str:
    blob = f"{grievance} {note}".lower()
    for shape, kws in _SHAPE_HINTS:
        if any(k in blob for k in kws):
            return shape
    return rng.choice(_SHAPES)


def _best_overlap(want: set[str], candidates: list, rng: random.Random):
    """The candidate (a Character/Faction/Location-like obj with .name/.description) whose name+desc
    shares the most tokens with `want` (APOPHENIA). Ties + no-overlap fall to a seeded pick, so the
    result is deterministic but never empty when candidates exist."""
    if not candidates:
        return None
    best, best_score = None, 0
    for obj in candidates:
        have = _toks(getattr(obj, "name", "")) | _toks(getattr(obj, "description", ""))
        score = len(want & have)
        if score > best_score:
            best, best_score = obj, score
    return best if best is not None else rng.choice(candidates)


def _easter_egg_ids(world: dict) -> set[str]:
    """Roster NPC ids flagged ``easter_egg: true`` — kept OUT of the default quest-giver pool so
    one oddball never becomes the system's go-to giver (e.g. Claudan the chaos-engine: a rare,
    opt-in find, never the spine's default voice). Setting-agnostic: any world flags its own."""
    roster = world.get("npc_roster") if isinstance(world, dict) else None
    if not isinstance(roster, list):
        return set()
    return {str(n.get("id")) for n in roster if isinstance(n, dict) and n.get("easter_egg") and n.get("id")}


def _derive_hooks(c: Campaign, world: dict, rng: random.Random, exclude: set[str]) -> list[QuestHook]:
    """Promote each resolved quest_outcome into a typed hook: its follow-on `hook` text is a wrong
    the world now contains (a grievance), bound to the campaign's own nouns via apophenia. NPCs in
    `exclude` (easter-egg givers) are never bound as a default giver/target."""
    qv = world.get("quest_variants") if isinstance(world, dict) else None
    qv_by_id = {q["id"]: q for q in qv if isinstance(q, dict) and q.get("id")} if isinstance(qv, list) else {}
    npcs = [ch for ch in c.characters.values()
            if getattr(ch, "kind", "") in ("npc", "companion") and ch.id not in exclude]
    factions = list(c.factions.values())
    places = list(c.locations.values())

    hooks: list[QuestHook] = []
    for qid, oid in (c.quest_outcomes or {}).items():
        q = qv_by_id.get(qid)
        if not isinstance(q, dict):
            continue
        outcome = next((o for o in q.get("outcomes", [])
                        if isinstance(o, dict) and o.get("id") == oid), None)
        if not isinstance(outcome, dict):
            continue
        note = str(outcome.get("hook") or "").strip()
        if not note:
            continue  # an outcome with no follow-on hook isn't a quest seed
        grievance = str(q.get("name") or qid).strip()
        want = _toks(grievance) | _toks(note)
        shape = _pick_shape(grievance, note, rng)
        giver = _best_overlap(want, npcs, rng)
        target = _best_overlap(want, (factions + npcs), rng)
        place = _best_overlap(want, places, rng)
        hooks.append(QuestHook(
            title=grievance,
            shape=shape,
            grievance=grievance,
            motivation=_MOTIVATION_BY_SHAPE.get(shape, "serenity"),
            giver_id=getattr(giver, "id", ""),
            target_id=getattr(target, "id", ""),
            place_id=getattr(place, "id", ""),
            note=note,
            status="open",
        ))
    return hooks


def _mark_spine(c: Campaign, hooks: list[QuestHook]) -> None:
    """Tag the most world-central hook as the SPINE (max token overlap with world_state facts);
    every other hook becomes a RIB that arcs back to it. With no world_state, the first hook leads."""
    if not hooks:
        return
    spine = hooks[0]
    ws = c.world_state
    if ws is not None and ws.facts:
        fact_toks = set()
        for k, v in ws.facts.items():
            fact_toks |= _toks(k) | _toks(v)
        best_score = -1
        for h in hooks:
            score = len(fact_toks & (_toks(h.grievance) | _toks(h.note)))
            if score > best_score:
                spine, best_score = h, score
    spine.spine = True
    for h in hooks:
        if h is not spine:
            h.arc_back = f"feeds the main arc: {spine.grievance}"


def _build_prelude(c: Campaign, hooks: list[QuestHook], rng: random.Random, exclude: set[str]) -> list[PreludeBeat]:
    """The guaranteed 4-beat cold-open. Binds a start place, a suggested companion + shared stake,
    and the spine grievance — so a session never opens mid-quest. The DM owns order/framing/prose.
    Easter-egg NPCs (`exclude`) are never the companion you 'meet' — the cold open stays canon."""
    start_place = c.current_location_id or (next(iter(c.locations), "") if c.locations else "")
    companions = [ch for ch in c.characters.values()
                  if getattr(ch, "kind", "") in ("npc", "companion") and ch.id not in exclude]
    companion = rng.choice(companions) if companions else None
    spine = next((h for h in hooks if h.spine), (hooks[0] if hooks else None))

    meet_note = rng.choice(_MEETING_STAKES)
    if companion is not None:
        meet_note = f"meet {companion.name}: {meet_note}"
    return [
        PreludeBeat(kind="arrival", note=rng.choice(_ARRIVAL_FRAMES), ref_id=start_place),
        PreludeBeat(kind="meeting", note=meet_note, ref_id=getattr(companion, "id", "")),
        PreludeBeat(
            kind="inciting_incident",
            note=(f"the wrong lands in front of the party — {spine.grievance}: {spine.note}"
                  if spine is not None else "a concrete wrong lands in front of the party"),
            ref_id=getattr(spine, "id", ""),
        ),
        PreludeBeat(
            kind="threshold",
            note="the party commits — the first thread goes live",
            ref_id=getattr(spine, "id", ""),
        ),
    ]


def generate(c: Campaign, world: dict, rng: random.Random) -> None:
    """Assemble ``c.quest_hooks`` + ``c.prelude`` from the seeded world. Mutates ``c`` in place.
    Runs at ``seed_world`` AFTER ``_resolve_quest_variants`` (so quest_outcomes + world_state are
    populated). Degrade-not-abort: any section that raises is skipped, never failing seed_world.
    Additive: a world with no quest_variants / no facts yields an empty graph (today's behavior)."""
    exclude = _easter_egg_ids(world)  # easter-egg givers (Claudan) kept out of the default flow
    try:
        hooks = _derive_hooks(c, world, rng, exclude)
        _mark_spine(c, hooks)
        c.quest_hooks = hooks
    except Exception as e:  # pragma: no cover - defensive; a bad source must not abort seed_world
        print(f"[questgen] skipping hook generation: {e!r}")
        hooks = list(c.quest_hooks)
    try:
        # Only build a cold-open when there's a world to open INTO (locations seeded). A bare
        # synthetic world with no locations leaves prelude empty == today's behavior.
        if c.locations:
            c.prelude = _build_prelude(c, hooks, rng, exclude)
    except Exception as e:  # pragma: no cover - defensive
        print(f"[questgen] skipping prelude generation: {e!r}")
