#!/usr/bin/env python3
"""export_campaign_artifacts.py — export a campaign's quests/npcs/locations/encounters as
structured artifact JSONs (HV2, Act II harvest loop — docs/roadmap/PRODUCT-ROADMAP.md §4c).

Sibling of qa/export_scene_grid.py: the SAME read-only-snapshot extractor pattern (import
servers/engine/models.py pydantic types read-only, never mutate, never save). Reuses
qa/distill.py's transcript reader (`distill.distill`) to pull NPC dialogue snippets and
combat start/end pairs out of a played campaign's stream-json transcript.

  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python qa/export_campaign_artifacts.py \\
      <campaign_id> [--out-dir qa/artifacts_out] [--transcript qa/transcripts/<run>.jsonl] \\
      [--run-id <run_id>] [--extracted-at <iso8601>]

Output layout: <out-dir>/<campaign_id>/{quests,npcs,locations,encounters}/*.json — one file per
artifact, each matching the common envelope in data/library/artifact_schema.json:
    {artifact_id, class, world, provenance{campaign_id, run_id, sha, extracted_at}, payload, scores: null}

Engine = SOLE WRITER: this script is read-only on engine state (snapshot.json is loaded via
server._require / store.load_campaign, never saved) and read-only on transcripts. It never
writes under play-state/qa/state — only under --out-dir (default qa/artifacts_out, gitignored).

`--extracted-at` is a caller-supplied string, NEVER derived from wall-clock time inside this
script, so re-running with the same argument is byte-for-byte reproducible (test determinism).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_MAX_DIALOGUE_SNIPPETS = 5
_MAX_QUEST_WRAPUP = 5
_ARTICLES = {"the", "a", "an"}

# A quoted span of NPC speech: straight or curly DOUBLE quotes, or CURLY single quotes. The
# straight single-quote (') is deliberately NOT a delimiter — in prose it is overwhelmingly a
# possessive/contraction ("Kervan's", "doesn't"), so treating it as a quote boundary captures
# garbage fragments. Curly typographic single quotes ('…') are unambiguous speech and kept.
_QUOTE_RE = re.compile(r"[\"“]([^\"”]{2,400})[\"”]|[‘]([^‘’]{2,400})[’]")
# Speech-attribution verbs that mark a quote as a character SPEAKING (vs the narrator merely
# mentioning them). Matches the DM prose conventions seen in real transcripts ("...", Roe says /
# Sefa: "..." / he growls). Deliberately narration-agnostic on tense/person.
_SPEECH_VERBS = (
    "say", "says", "said", "ask", "asks", "asked", "reply", "replies", "replied",
    "growl", "growls", "growled", "whisper", "whispers", "whispered", "mutter", "mutters",
    "muttered", "snap", "snaps", "snapped", "hiss", "hisses", "hissed", "call", "calls",
    "called", "bark", "barks", "barked", "rasp", "rasps", "rasped", "drawl", "drawls",
    "drawled", "murmur", "murmurs", "murmured", "sigh", "sighs", "sighed", "spit", "spits",
    "spat", "roar", "roars", "roared", "add", "adds", "added", "continue", "continues",
    "demand", "demands", "demanded", "purr", "purrs", "purred", "sneer", "sneers", "sneered",
    "offer", "offers", "offered", "tell", "tells", "told", "answer", "answers", "answered",
)
# Resolution language that marks a narration beat as a quest's WRAP-UP (its outcome/consequence),
# so the closing beats — not every mid-quest mention — are what land in the quest payload.
_RESOLUTION_CUES = (
    "resolv", "complet", "finish", "conclud", "outcome", "consequence", "aftermath", "settle",
    "in the end", "at last", "closed", "sealed the", "the deal", "repaid", "repay", "avenged",
    "betray", "the price", "the cost", "spared", "killed", "died", "dead", "survived", "saved",
    "abandon", "failed", "fell through", "kept the", "broke the", "delivered", "returned the",
    "the reward", "vengeance", "reckoning", "the debt",
)


# ── engine + distill imports (read-only) ─────────────────────────────────────────────────────
def _import_engine():
    sys.path.insert(0, str(_ROOT / "servers" / "engine"))
    import server  # noqa: PLC0415
    import scene_grid as sg  # noqa: PLC0415

    return server, sg


def _import_distill():
    sys.path.insert(0, str(_ROOT / "qa"))
    import distill  # noqa: PLC0415

    return distill


# ── provenance / ids ──────────────────────────────────────────────────────────────────────────
def _derive_run_id(campaign_id: str, explicit_run_id: Optional[str], state_dir: Optional[str]) -> Optional[str]:
    """The qa harness convention (qa/run_duo.sh): STATE_DIR="qa/state/$RUN", so the state
    directory's basename IS the run_id. Best-effort: an explicit --run-id always wins; else,
    when WORLDOS_STATE_DIR points inside qa/state/<run_id>/..., recover <run_id> from the path.
    Returns None when neither is available (e.g. a hand-run campaign outside the harness) —
    never guessed from the campaign_id itself."""
    if explicit_run_id:
        return explicit_run_id
    if not state_dir:
        return None
    parts = Path(state_dir).resolve().parts
    for i, p in enumerate(parts):
        if p == "state" and i > 0 and parts[i - 1] == "qa" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def _make_provenance(campaign_id: str, run_id: Optional[str], sha: Optional[str], extracted_at: str) -> dict:
    return {"campaign_id": campaign_id, "run_id": run_id, "sha": sha or None, "extracted_at": extracted_at}


def _envelope(artifact_id: str, cls: str, world: str, provenance: dict, payload: dict) -> dict:
    return {
        "artifact_id": artifact_id,
        "class": cls,
        "world": world,
        "provenance": provenance,
        "payload": payload,
        "scores": None,
    }


# ── transcript helpers ───────────────────────────────────────────────────────────────────────
def _load_transcript_lines(transcript_path: Optional[Path], *, explicit: bool = False) -> list[str]:
    """Read non-blank lines from a transcript. A missing/absent transcript falls back to []
    (no encounters/dialogue) ONLY for the omitted/inferred case; an EXPLICIT --transcript path
    that doesn't exist is a caller mistake and raises rather than silently harvesting nothing."""
    if transcript_path is None:
        return []
    if not transcript_path.exists():
        if explicit:
            raise FileNotFoundError(f"--transcript path does not exist: {transcript_path}")
        return []
    return [ln for ln in transcript_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _dm_player_text_blocks(lines: list[str]) -> list[str]:
    """Every assistant text block (DM/player narration) in the transcript, in order. Reuses
    distill's own event-shape tolerance (a line that fails to parse is skipped, not raised)."""
    distill = _import_distill()
    out: list[str] = []
    for raw in lines:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        for b in distill._content_blocks(ev.get("message", {})):
            if b.get("type") == "text":
                txt = b.get("text", "").strip()
                if txt:
                    out.append(txt)
    return out


def _npc_dialogue_snippets(name: str, text_blocks: list[str], limit: int = _MAX_DIALOGUE_SNIPPETS) -> list[str]:
    """Up to `limit` narration snippets that mention this NPC by name — a cheap, transcript-
    reading proxy for "moments this NPC appeared in the story" (no NLP, no engine dependency;
    a plain case-insensitive whole-word-ish substring match). First name only (e.g. "Jaheira"
    out of "Jaheira" or "Minsc and Boo" -> "Minsc") so a two-word canon name still matches.
    Leading articles are skipped ("The Emperor" -> "Emperor") so an NPC whose display name
    starts with "The"/"A"/"An" doesn't match almost every narration block via the article."""
    if not name:
        return []
    words = name.split()
    first = next((w for w in words if w.lower() not in _ARTICLES), words[0])
    # word-boundary match so "Boo" doesn't fire on "book" and a short/article name is exact
    pat = re.compile(rf"\b{re.escape(first)}\b", re.IGNORECASE)
    out: list[str] = []
    for blk in text_blocks:
        if pat.search(blk):
            out.append(blk if len(blk) <= 400 else blk[:399] + "…")
            if len(out) >= limit:
                break
    return out


def _quote_spans(block: str) -> list[tuple[int, int, str]]:
    """Every quoted span in a narration block as (start, end, text). Handles straight and curly
    double/single quotes. The offsets let the attribution check look at the words immediately
    around a quote (who is speaking) rather than the whole block."""
    spans: list[tuple[int, int, str]] = []
    for m in _QUOTE_RE.finditer(block):
        txt = m.group(1) if m.group(1) is not None else m.group(2)
        # Strip markdown emphasis markers (* _) that bracket italicised in-fiction speech and
        # collapse internal whitespace, so the captured line is the clean spoken text.
        cleaned = " ".join(txt.replace("*", " ").replace("_", " ").split()) if txt else ""
        # Keep only quotes with real words (>=2 word chars) — a bare fragment like a name tag or a
        # stray punctuation quote isn't a spoken line.
        if len(re.findall(r"\w", cleaned)) >= 2:
            spans.append((m.start(), m.end(), cleaned))
    return spans


_SPEECH_VERB_RE = re.compile(r"\b(?:%s)\b" % "|".join(_SPEECH_VERBS), re.IGNORECASE)


def _attributed_to(block: str, span: tuple[int, int, str], name_pat: re.Pattern) -> bool:
    """True when the quoted span at `span` is attributed to the NPC whose first name matches
    `name_pat`. Attribution reads TIGHT in DM prose — `"...", Roe says` (name+verb right after the
    close-quote) or `Sefa says: "..."` (name+verb right before the open-quote) — so we require the
    NPC name AND a speech verb to co-occur in a SHORT window on ONE side of the quote (~45 chars),
    not merely somewhere in the surrounding paragraph. This is the difference between an IN-VOICE
    line the NPC spoke and a block that only NAMES the NPC in third-person scene/combat narration
    (the old mention-matcher's false positive, which caps voice_distinctiveness under the rubric),
    and it stops a quote from being mis-attributed to a second NPC merely named elsewhere in the
    same paragraph."""
    start, end, _ = span
    after = block[end:end + 60]
    before = block[max(0, start - 60):start]
    for side in (after, before):
        if name_pat.search(side) and _SPEECH_VERB_RE.search(side):
            return True
    return False


def _npc_voice_lines(name: str, text_blocks: list[str], limit: int = _MAX_DIALOGUE_SNIPPETS) -> list[str]:
    """Up to `limit` IN-VOICE quoted lines this NPC actually SPOKE in the campaign — the thing the
    NPC rubric rewards (voice_distinctiveness: 'could an actor hear how they talk?'). A quoted span
    is kept only when the NPC's (leading-article-stripped) first name and a speech-attribution verb
    both sit in the window around the quote, so third-person narration that merely mentions the NPC
    (the old matcher's false positives — combat rosters, scene-setting) is excluded. Deduped in
    first-seen order; a silent NPC (no attributed quotes) returns [] and stays a valid artifact."""
    if not name:
        return []
    # Match ANY significant name token (first OR last), so both `"...", Corren says` and
    # `"...", Hask says` attribute to "Corren Hask". Articles and 1-char tokens are dropped so a
    # name like "The Emperor" matches on "Emperor", never the near-ubiquitous article.
    tokens = [w for w in name.split() if w.lower() not in _ARTICLES and len(w) > 1]
    if not tokens:
        tokens = [name.split()[0]]
    name_pat = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(t) for t in tokens), re.IGNORECASE)
    out: list[str] = []
    seen: set[str] = set()
    for blk in text_blocks:
        for span in _quote_spans(blk):
            _, _, quote = span
            if not _attributed_to(blk, span, name_pat):
                continue
            # Drop a fragment that opens mid-sentence (lowercase first letter) — that's a slice of
            # narration spliced through a quote mark, not a fresh spoken line. A quote opening with
            # a capital, a digit, or opening punctuation (— … ') reads as real delivered speech.
            head = quote.lstrip("—–-…‘'\"“ ")
            if head and head[0].isalpha() and not head[0].isupper():
                continue
            line = quote if len(quote) <= 400 else quote[:399] + "…"
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(line)
            if len(out) >= limit:
                return out
    return out


def _quest_wrap_up(quest_title: str, text_blocks: list[str], limit: int = _MAX_QUEST_WRAPUP) -> list[str]:
    """Up to `limit` closing narration beats that describe THIS quest's resolution/outcome — the
    wrap-up context the Questwright rubric scores for consequence_weight / narrative completeness.
    A beat qualifies only when it names the quest (leading-article-stripped title token match) AND
    carries resolution language (_RESOLUTION_CUES), so mid-quest mentions don't dilute the outcome.
    Deduped in first-seen order; a quest with no mined wrap-up beats returns [] (graceful)."""
    title = (quest_title or "").strip()
    if not title:
        return []
    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z'’]+", title) if t.lower() not in _ARTICLES]
    if not tokens:
        return []
    title_pat = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(t) for t in tokens), re.IGNORECASE)
    out: list[str] = []
    seen: set[str] = set()
    for blk in text_blocks:
        if not title_pat.search(blk):
            continue
        # Scan for resolution cues with the quest TITLE tokens masked out, so a cue phrase that is
        # merely part of the title itself ("the price"/"the debt" inside a title like "The Price of
        # Silence") never counts as resolution language — the beat must carry an outcome word of
        # its OWN, not echo the quest name.
        low = title_pat.sub(" ", blk).lower()
        if not any(cue in low for cue in _RESOLUTION_CUES):
            continue
        # Same cap as the NPC voice-line/dialogue-snippet truncation above (_npc_dialogue_snippets,
        # _npc_voice_lines) — one consistent payload-size ceiling across the extractor, not a
        # second one-off limit.
        beat = blk if len(blk) <= 400 else blk[:399] + "…"
        key = beat.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(beat)
        if len(out) >= limit:
            break
    return out


def _coerce_id_list(raw) -> list[str]:
    """Mirror the engine's StrListArg coercion (models._coerce_list) on a RAW transcript arg:
    the transcript records the tool_use input BEFORE pydantic's BeforeValidator runs, so a DM
    that passed combatant_ids as a bare or comma-separated string ("a" / "a,b") is logged as a
    string, not a list. Iterating that string directly would yield single-character "names"."""
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        return [t.strip() for t in s.split(",") if t.strip()] if "," in s else [s]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _errored_tool_use_ids(lines: list[str]) -> set[str]:
    """tool_use_ids whose tool_result came back is_error=true — a rejected/failed engine call.
    Its start_combat never actually opened an encounter, so it must not be harvested. The result
    lives in a later `user` event, keyed by tool_use_id (distill._content_blocks tolerance)."""
    errored: set[str] = set()
    for raw in lines:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "user":
            continue
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                tid = b.get("tool_use_id")
                if tid:
                    errored.add(tid)
    return errored


def _combat_encounters_from_transcript(lines: list[str], characters: dict) -> list[dict]:
    """Pairs each start_combat tool call with its matching end_combat (composition from
    start_combat's combatant_ids resolved to character names; outcome from end_combat's
    resolution text, which is the same string the engine stamps onto
    Campaign.last_combat_resolution). A start_combat with no later end_combat in this
    transcript yields outcome="" (combat still open / transcript truncated mid-fight) rather
    than being dropped — every started encounter is accounted for, including a consecutive
    start_combat (no intervening end_combat): the earlier one is flushed as a dangling
    outcome="" encounter before the new one overwrites `pending`. start_combat calls whose
    tool_result came back is_error=true (rejected engine call) are skipped — no fake encounter."""
    errored = _errored_tool_use_ids(lines)
    encounters: list[dict] = []
    pending: Optional[list[str]] = None
    idx = 0

    def flush(names: list[str], outcome: str) -> None:
        nonlocal idx
        idx += 1
        encounters.append(
            {"id": f"encounter-{idx}", "composition": [{"name": n} for n in names], "outcome": outcome}
        )

    for raw in lines:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name = b.get("name", "")
            args = b.get("input", {}) or {}
            if name.endswith("start_combat"):
                if b.get("id") in errored:
                    continue  # rejected engine call — never actually opened an encounter
                if pending is not None:
                    flush(pending, "")  # dangling: previous combat never got an end_combat
                ids = _coerce_id_list(args.get("combatant_ids"))
                pending = [characters.get(i, {}).get("name", i) for i in ids]
            elif name.endswith("end_combat") and pending is not None:
                flush(pending, args.get("resolution", ""))
                pending = None
    if pending is not None:
        flush(pending, "")
    return encounters


# ── per-class extraction ─────────────────────────────────────────────────────────────────────
def _npc_final_status(c: dict) -> str:
    """dead > stable > downed > active. A creature at current_hp<=0 that is neither `dead`
    nor `stable` is still unconscious/dying (Character.dead/stable are both False until it
    finishes death saves) — reporting it as "active" would misrepresent a downed NPC."""
    if c.get("dead"):
        return "dead"
    if c.get("stable"):
        return "stable"
    if c.get("current_hp", 1) <= 0:
        return "downed"
    return "active"



def _quest_consequences(quest_id: str, quest_title: str, consequences: list) -> list[dict]:
    """Best-effort linkage: the engine's Consequence has no direct quest_id FK (it is keyed by
    world-sim thread_id, not per-quest — see Campaign.consequences / thread model), so a
    consequence is attributed to a quest only when its free-text note/text mentions the quest's
    id or title. Conservative by design: no false-positive FK is invented; an unmatched
    consequence simply isn't attached to any quest."""
    out = []
    needle_id = quest_id.lower()
    needle_title = (quest_title or "").strip().lower()
    for c in consequences:
        text = f"{c.get('note', '')} {c.get('text', '')}".lower()
        if (needle_id and needle_id in text) or (needle_title and needle_title in text):
            out.append({"id": c.get("id", ""), "trigger_day": c.get("trigger_day"), "text": c.get("text", "")})
    return out


def extract_quests(campaign: dict, provenance_base, world: str, text_blocks: list[str]) -> list[dict]:
    artifacts = []
    quests = campaign.get("quests") or {}
    consequences = campaign.get("consequences") or []
    for qid, q in quests.items():
        title = q.get("title", "")
        # Narrative wrap-up (HV2 #1329): the quest's OWN description (Quest.description, previously
        # dropped) plus the closing transcript beats that describe its outcome — the resolution
        # context the Questwright rubric scores for consequence_weight / narrative completeness.
        wrap_up = _quest_wrap_up(title, text_blocks)
        payload = {
            "id": q.get("id", qid),
            "name": title,
            "description": q.get("description", ""),
            "objectives": q.get("objectives", []),
            "completed_objectives": q.get("completed_objectives", []),
            "resolution_status": q.get("status", ""),
            "resolution": {
                "status": q.get("status", ""),
                "evolves_to": q.get("evolves_to", ""),
                "callback_in_days": q.get("callback_in_days", 0),
                "wrap_up": wrap_up,
            },
            "evolves_to": q.get("evolves_to", ""),
            "consequences": _quest_consequences(qid, title, consequences),
        }
        artifacts.append(
            _envelope(f"quest:{campaign['id']}:{qid}", "quest", world, provenance_base(), payload)
        )
    return artifacts


def extract_npcs(campaign: dict, provenance_base, world: str, text_blocks: list[str]) -> list[dict]:
    artifacts = []
    characters = campaign.get("characters") or {}
    for cid, c in characters.items():
        # A roster character the party recruits is promoted to kind="companion" (servers/engine/
        # content.py:834) — still a met, harvestable NPC-shaped artifact, just no longer kind=="npc".
        # Exclude only players/monsters, not the class of character that got recruited.
        if c.get("kind") not in ("npc", "companion"):
            continue
        log = c.get("approval_log") or []
        if log:
            first = log[0]
            start_val = first.get("new_value", 0) - first.get("delta", 0)
        else:
            start_val = c.get("attitude_value", 0)
        end_val = c.get("attitude_value", 0)
        personality = {
            k: c.get(k, "")
            for k in ("personality", "appearance", "mannerisms", "backstory")
            if c.get(k)
        }
        payload = {
            "id": c.get("id", cid),
            "name": c.get("name", ""),
            "voice_id": c.get("voice_id", ""),
            "personality": personality,
            "attitude_arc": {"start": start_val, "end": end_val},
            "final_status": _npc_final_status(c),
            # IN-VOICE quoted lines the NPC actually SPOKE (HV2 #1329). The rubric rewards a
            # VOICED NPC and forces voice_distinctiveness <= 2 on "described-not-voiced" — so we
            # deliberately DON'T fall back to the old mention-based proxy, which harvested
            # third-person combat/scene narration (a party roster line naming the NPC is NOT its
            # voice). A silent NPC surfaces [] and stays a valid artifact (graceful), rather than
            # carrying noise that actively HURTS the panel score.
            "dialogue_snippets": _npc_voice_lines(c.get("name", ""), text_blocks),
        }
        artifacts.append(_envelope(f"npc:{campaign['id']}:{cid}", "npc", world, provenance_base(), payload))
    return artifacts


def extract_locations(campaign: dict, provenance_base, world: str, sg_module, campaign_id: str) -> list[dict]:
    artifacts = []
    locations = campaign.get("locations") or {}
    for lid, loc in locations.items():
        grid_payload = None
        grid = loc.get("scene_grid")
        if grid is not None:
            grid_payload = _scene_grid_payload(grid, loc, sg_module)
        payload = {
            "id": loc.get("id", lid),
            "name": loc.get("name", ""),
            "description": loc.get("description", ""),
            "scene_grid": grid_payload,
            "visited": loc.get("visited", False),
        }
        artifacts.append(
            _envelope(f"location:{campaign['id']}:{lid}", "location", world, provenance_base(), payload)
        )
    return artifacts


class _GridShim:
    """A thin attribute-access wrapper over the raw scene_grid dict so scene_grid.impassable_cells
    (which reads attributes: .cell_default.walkable, .cells[].c/.r/.walkable, .props[].cells) can be
    reused verbatim on a snapshot dict — the impassable projection is then IDENTICAL to what
    export_scene_grid.py emits (same function, not a re-implementation), not merely "the same keys"."""

    def __init__(self, grid: dict):
        self.cell_default = types.SimpleNamespace(walkable=bool((grid.get("cell_default") or {}).get("walkable", True)))
        self.cells = [
            types.SimpleNamespace(c=cell.get("c"), r=cell.get("r"),
                                  walkable=cell.get("walkable", True), type=cell.get("type", ""))
            for cell in grid.get("cells", [])
        ]
        self.props = [types.SimpleNamespace(cells=[tuple(pair) for pair in p.get("cells", [])]) for p in grid.get("props", [])]


def _scene_grid_payload(grid: dict, loc: dict, sg_module) -> dict:
    """Reuses export_scene_grid's field mapping directly on the raw dict snapshot (NOT a
    fork/re-derivation — same keys, same walls/props projection) plus the SAME impassable
    derivation (scene_grid.impassable_cells via _GridShim), so a location's scene_grid artifact
    carries the exact walls/props/impassable geometry qa/export_scene_grid.py would emit for it.

    NOTE (deliberate divergence from export_scene_grid.py, which is a pre-GREYBOX write gate):
    this is a read-only HARVEST of already-played state, so it does NOT run the pre-greybox
    validation gate (sg.validate_scene_grid) — a played room's grid is a fait accompli, not an
    about-to-be-rendered candidate to refuse. The greybox-only `material` hint and the location
    `name` are likewise omitted here (the location artifact carries name at the envelope level)."""
    cols = grid["grid"]["cols"]
    rows = grid["grid"]["rows"]
    walls = [
        [cell["c"], cell["r"]]
        for cell in grid.get("cells", [])
        if cell.get("type") == "wall" or not cell.get("walkable", True)
    ]
    props = [{"kind": p.get("kind", "prop"), "cells": [list(pair) for pair in p.get("cells", [])]} for p in grid.get("props", [])]
    impassable = sg_module.impassable_cells(_GridShim(grid), cols, rows)
    return {
        "cols": cols,
        "rows": rows,
        "cell_default_walkable": bool((grid.get("cell_default") or {}).get("walkable", True)),
        "walls": walls,
        "props": props,
        "impassable": impassable,
        "door_cells": [list(pair) for pair in (grid.get("door_cells") or [])],
        "protected_lane_cells": [list(pair) for pair in (grid.get("protected_lane_cells") or [])],
    }


def extract_encounters(campaign: dict, provenance_base, world: str, transcript_lines: list[str]) -> list[dict]:
    characters = campaign.get("characters") or {}
    raw_encounters = _combat_encounters_from_transcript(transcript_lines, characters)
    artifacts = []
    for enc in raw_encounters:
        artifacts.append(
            _envelope(f"encounter:{campaign['id']}:{enc['id']}", "encounter", world, provenance_base(), enc)
        )
    return artifacts


# ── orchestration ────────────────────────────────────────────────────────────────────────────
def build_artifacts(
    campaign_dict: dict,
    *,
    run_id: Optional[str],
    extracted_at: str,
    transcript_lines: list[str],
    sg_module,
) -> dict[str, list[dict]]:
    """Pure function: campaign snapshot (as a dict) + transcript lines -> {class: [artifact, ...]}.
    No I/O — the caller loads the snapshot / transcript and writes the output."""
    campaign_id = campaign_dict["id"]
    world = campaign_dict.get("world_id", "")
    sha = campaign_dict.get("engine_sha") or None

    def provenance_base():
        return _make_provenance(campaign_id, run_id, sha, extracted_at)

    text_blocks = _dm_player_text_blocks(transcript_lines)
    return {
        "quests": extract_quests(campaign_dict, provenance_base, world, text_blocks),
        "npcs": extract_npcs(campaign_dict, provenance_base, world, text_blocks),
        "locations": extract_locations(campaign_dict, provenance_base, world, sg_module, campaign_id),
        "encounters": extract_encounters(campaign_dict, provenance_base, world, transcript_lines),
    }


def _artifact_filename(artifact_id: str) -> str:
    """A stable, filesystem-safe filename derived from the artifact_id. The slug substitution
    (`[^A-Za-z0-9_.-]+` -> `_`) is NOT injective, so two distinct ids could collide; a short
    hash suffix of the RAW id keeps the filename 1:1 with the artifact_id (no silent overwrite)."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", artifact_id)
    digest = hashlib.sha1(artifact_id.encode("utf-8")).hexdigest()[:8]
    return f"{slug}.{digest}.json"


def write_artifacts(out_dir: Path, campaign_id: str, artifacts_by_class: dict[str, list[dict]]) -> dict[str, int]:
    counts = {}
    campaign_out = out_dir / campaign_id
    for cls, artifacts in artifacts_by_class.items():
        cls_dir = campaign_out / cls
        # Clear any prior extraction for this class so a re-run into the same out-dir never
        # leaves orphaned JSONs from artifacts that no longer exist (removed quest/NPC/location).
        if cls_dir.exists():
            for stale in cls_dir.glob("*.json"):
                stale.unlink()
        cls_dir.mkdir(parents=True, exist_ok=True)
        seen: dict[str, str] = {}
        for art in artifacts:
            fname = _artifact_filename(art["artifact_id"])
            prior = seen.get(fname)
            if prior is not None and prior != art["artifact_id"]:  # 1:1 guarantee tripped
                raise ValueError(f"artifact filename collision: {fname!r} for {prior!r} and {art['artifact_id']!r}")
            seen[fname] = art["artifact_id"]
            (cls_dir / fname).write_text(json.dumps(art, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        counts[cls] = len(artifacts)
    return counts


def _resolve_transcript_path(campaign_id: str, run_id: Optional[str], explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        return Path(explicit)
    if run_id:
        candidate = _ROOT / "qa" / "transcripts" / f"{run_id}.jsonl"
        if candidate.exists():
            return candidate
    return None


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("campaign_id")
    ap.add_argument("--out-dir", default=str(_ROOT / "qa" / "artifacts_out"))
    ap.add_argument("--transcript", default=None, help="explicit transcript .jsonl path (default: qa/transcripts/<run_id>.jsonl)")
    ap.add_argument("--run-id", default=None, help="override run_id derivation (default: inferred from WORLDOS_STATE_DIR)")
    ap.add_argument("--extracted-at", required=True, help="caller-supplied timestamp string; never wall-clock-derived here")
    args = ap.parse_args(argv)

    server, sg = _import_engine()
    campaign_obj = server._require(args.campaign_id)  # read-only: store.load_campaign, no save
    campaign_dict = json.loads(campaign_obj.model_dump_json())

    state_dir = os.environ.get("WORLDOS_STATE_DIR")
    run_id = _derive_run_id(args.campaign_id, args.run_id, state_dir)
    transcript_path = _resolve_transcript_path(args.campaign_id, run_id, args.transcript)
    transcript_lines = _load_transcript_lines(transcript_path, explicit=bool(args.transcript))

    artifacts_by_class = build_artifacts(
        campaign_dict, run_id=run_id, extracted_at=args.extracted_at, transcript_lines=transcript_lines, sg_module=sg
    )
    counts = write_artifacts(Path(args.out_dir), args.campaign_id, artifacts_by_class)

    total = sum(counts.values())
    print(
        f"[export_campaign_artifacts] {args.campaign_id} (run_id={run_id!r}, transcript={transcript_path}): "
        f"{counts} ({total} artifacts) -> {args.out_dir}/{args.campaign_id}/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
