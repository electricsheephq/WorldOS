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

import library
from models import Campaign, PreludeBeat, QuestHook

# HV4 (#1326): a library candidate that ties the best NATIVE overlap within this many tokens is
# eligible for the tier tie-break (epic addendum [HIGH]: tier breaks a NEAR-tie only; a strictly
# higher overlap always wins). Kept small so a barely-related library quest never displaces a
# well-matched native seed.
_LIBRARY_TIE_TOKENS = 1

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
_SHAPES = [
    "fetch_plus", "investigation", "hunt", "rescue", "heist", "escort", "faction_war", "dilemma",
    # gut-punch shapes — moral weight, tragedy, Baldur's-Gate-caliber stakes
    "false_accusation", "sacrifice_choice", "revelation", "tragedy_unfolding",
]
# A shape's fitting NPC motivation (the "why"), from Doran/Parberry's 9 — used as a label only.
_MOTIVATION_BY_SHAPE = {
    "fetch_plus": "equipment", "investigation": "knowledge", "hunt": "protection",
    "rescue": "serenity", "heist": "wealth", "escort": "protection",
    "faction_war": "conquest", "dilemma": "reputation",
    # gut-punch shapes
    "false_accusation": "reputation",   # clearing (or condemning) a name; truth is political
    "sacrifice_choice": "serenity",     # saving one thing at the cost of another; no clean win
    "revelation": "knowledge",          # truth that recontextualises everything already done
    "tragedy_unfolding": "comfort",     # softening / witnessing a doom already in motion
}
# Light keyword heuristics → a dramatic shape (else a seeded pick). Bounded, not a grammar.
_SHAPE_HINTS = [
    ("rescue", ("missing", "captive", "captured", "taken", "hostage", "rescue", "freed", "enslaved", "shackled")),
    ("investigation", ("murder", "who", "secret", "rune", "cipher", "unmasked", "informant", "asking why", "schematic")),
    ("heist", ("smuggle", "steal", "stolen", "vault", "control-core", "relic", "looted", "buy back")),
    ("faction_war", ("faction", "rule", "throne", "archduke", "regime", "resistance", "patrol", "occupation", "collabor")),
    ("hunt", ("hunt", "kill", "beast", "broke", "broken", "warband", "crusade", "jailers")),
    ("escort", ("escort", "guard", "convoy", "refugee", "smuggling out", "get them out")),
    # gut-punch hints (checked after task-flavored shapes so they win only when lore signals them)
    ("false_accusation", ("blamed", "accused", "framed", "falsely", "scapegoat", "exile", "innocent", "wrongly", "hanged")),
    ("sacrifice_choice", ("choose", "choice", "cost", "price", "either", "trade", "lose", "give up", "cannot save both")),
    ("revelation", ("truth", "revealed", "betrayal", "hidden", "identity", "always been", "all along", "lie at", "lied")),
    ("tragedy_unfolding", ("doom", "already", "dying", "falling", "cannot stop", "too late", "last", "inevit", "witness")),
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


def _non_meetable_ids(world: dict) -> set[str]:
    """Roster NPC ids flagged ``prelude_meetable: false`` — kept OUT of the cold-open MEETING
    pool only (F05-10). These are villains / deities / patrons (Raphael, Withers, The Emperor in
    BG) who canonically appear LATER and whose generic "you meet them in a scuffle" cold-open is
    tonal mis-staging — a third of real seeded BG campaigns opened on one. They stay fully valid
    quest givers / targets / canon NPCs; only the random ``_build_prelude`` companion draw skips
    them. ADDITIVE + setting-agnostic: a roster with no such flag yields an empty set, so a
    flagless world's prelude distribution is byte-identical to today's. ``prelude_meetable``
    DEFAULTS to true — only an EXPLICIT ``false`` excludes."""
    roster = world.get("npc_roster") if isinstance(world, dict) else None
    if not isinstance(roster, list):
        return set()
    return {
        str(n.get("id"))
        for n in roster
        if isinstance(n, dict) and n.get("prelude_meetable") is False and n.get("id")
    }


def _bind_hook(grievance: str, note: str, npcs: list, factions: list, places: list,
               rng: random.Random, *, source: str = "", tier: str = "") -> QuestHook:
    """Assemble ONE hook from a grievance + note, binding the campaign's own nouns via apophenia
    (the shared path both native quest_outcomes and HV4 library candidates go through, so a library
    hook reads identically to a native one). ``source``/``tier`` are provenance the engagement scorer
    reads to tell a library-sourced hook from a fresh-generated one (default "" == native)."""
    want = _toks(grievance) | _toks(note)
    shape = _pick_shape(grievance, note, rng)
    return QuestHook(
        title=grievance,
        shape=shape,
        grievance=grievance,
        motivation=_MOTIVATION_BY_SHAPE.get(shape, "serenity"),
        giver_id=getattr(_best_overlap(want, npcs, rng), "id", ""),
        target_id=getattr(_best_overlap(want, (factions + npcs), rng), "id", ""),
        place_id=getattr(_best_overlap(want, places, rng), "id", ""),
        note=note,
        status="open",
        source=source,
        tier=tier,
    )


def _library_hooks(world: dict, native_ids: set[str], npcs: list, factions: list, places: list,
                   rng: random.Random) -> list[QuestHook]:
    """HV4 (#1326): the LIBRARY candidate source for _derive_hooks — DEFAULT-OFF.

    Only fires when the world opts in via ``library_packs`` (library.load_pool gates on it; an
    empty pool -> [] -> byte-identical seed path). Each promoted ``quest`` entry becomes a hook
    bound to this campaign's nouns, tagged ``source="library"`` + its tier. COLLISION/PRECEDENCE
    (epic addendum [MED], per the bestiary-pack precedent): a native quest_variants id ALWAYS
    wins — a library candidate whose artifact_id collides with a native quest id is EXCLUDED
    (library is additive, never overriding). The pool is already tier-sorted (canonical > stable
    > fresh-gen) so a same-id collision across packs resolves to the highest tier. Degrade-not-
    abort: a malformed entry is skipped, never raising."""
    pool = library.load_pool(world, "quest")  # _library_dir reads world["_library_root"] if set
    if not pool:
        return []  # DEFAULT-OFF: no packs configured / no matching pack on disk
    hooks: list[QuestHook] = []
    seen: set[str] = set()
    for entry in pool:
        aid = str(entry.get("artifact_id") or "").strip()
        # A native quest_variants id always wins (additive, never overriding); a duplicate library
        # id across packs keeps only the first (already the highest tier via the sorted pool).
        if not aid or aid in native_ids or aid in seen:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue  # an entry with no reusable payload can't seed a hook — skip
        grievance = str(payload.get("name") or payload.get("title") or payload.get("grievance") or "").strip()
        note = str(payload.get("hook") or payload.get("note") or payload.get("description") or "").strip()
        if not grievance or not note:
            continue  # no wrong / no seed detail — not a usable quest seed
        seen.add(aid)
        hooks.append(_bind_hook(grievance, note, npcs, factions, places, rng,
                                source="library", tier=str(entry.get("tier") or "")))
    return hooks


def _derive_hooks(c: Campaign, world: dict, rng: random.Random, exclude: set[str]) -> list[QuestHook]:
    """Promote each resolved quest_outcome into a typed hook: its follow-on `hook` text is a wrong
    the world now contains (a grievance), bound to the campaign's own nouns via apophenia. NPCs in
    `exclude` (easter-egg givers) are never bound as a default giver/target.

    HV4 (#1326): when the world opts in via ``library_packs``, promoted library quests are appended
    as ADDITIONAL candidates (default-off — no packs -> byte-identical to today). Tier acts as a
    TIE-BREAK only: library hooks are appended AFTER the native ones, so the native seeds (and the
    spine pick) are unperturbed, and a library candidate colliding with a native quest id is dropped
    (native always wins). See _library_hooks."""
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
        hooks.append(_bind_hook(grievance, note, npcs, factions, places, rng))
    # HV4: append library candidates (default-off). Native quest ids win a collision — pass the
    # resolved native quest ids so a library entry can never override a native seed.
    hooks.extend(_library_hooks(world, set(qv_by_id), npcs, factions, places, rng))
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


def _build_prelude(
    c: Campaign,
    hooks: list[QuestHook],
    rng: random.Random,
    exclude: set[str],
    meet_exclude: set[str] | None = None,
) -> list[PreludeBeat]:
    """The guaranteed 4-beat cold-open. Binds a start place, a suggested companion + shared stake,
    and the spine grievance — so a session never opens mid-quest. The DM owns order/framing/prose.
    Easter-egg NPCs (`exclude`) are never the companion you 'meet' — the cold open stays canon.

    F05-10: ``meet_exclude`` additionally bars villain/deity/patron roster NPCs (flagged
    ``prelude_meetable: false`` — Raphael / Withers / The Emperor in BG) from the MEETING pool,
    because a generic "you meet them in a scuffle" cold-open mis-stages a figure who canonically
    arrives later. They remain valid quest givers/targets — only this random draw skips them.
    When ``meet_exclude`` is None/empty (a flagless world), the pool and distribution are
    byte-identical to today's. Among the remaining eligible NPCs, prefer the one whose name/desc
    most overlaps the spine grievance (deterministic apophenia), so the 'meet' is thematically
    coupled to the story instead of a bare uniform draw — falling back to a seeded pick on a tie."""
    spine = next((h for h in hooks if h.spine), (hooks[0] if hooks else None))
    start_place = c.current_location_id or (next(iter(c.locations), "") if c.locations else "")
    bar = set(exclude) | set(meet_exclude or set())
    companions = [ch for ch in c.characters.values()
                  if getattr(ch, "kind", "") in ("npc", "companion") and ch.id not in bar]
    # Prefer the eligible NPC most thematically tied to the spine grievance; uniform fallback.
    if companions and spine is not None:
        want = _toks(getattr(spine, "grievance", "")) | _toks(getattr(spine, "note", ""))
        companion = _best_overlap(want, companions, rng) if want else rng.choice(companions)
    else:
        companion = rng.choice(companions) if companions else None

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
    # F05-10: villain/deity/patron NPCs flagged prelude_meetable:false are kept out of the
    # cold-open MEETING pool only (they stay valid givers/targets). Empty for a flagless world.
    meet_exclude = exclude | _non_meetable_ids(world)
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
            c.prelude = _build_prelude(c, hooks, rng, exclude, meet_exclude)
    except Exception as e:  # pragma: no cover - defensive
        print(f"[questgen] skipping prelude generation: {e!r}")
