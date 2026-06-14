"""Spellcasting helpers — load bundled SRD spell mechanics and resolve a cast's
effect (damage / heal expression) given the slot level used, the caster's level
(for cantrip scaling), and the casting modifier (for heals). Pure + testable;
the engine's cast_spell tool wraps these with slot/concentration state.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Optional

_SPELLS_PATH = Path(__file__).resolve().parents[2] / "data" / "srd" / "spells.json"


@functools.lru_cache(maxsize=None)
def _all() -> dict:
    rows = json.loads(_SPELLS_PATH.read_text(encoding="utf-8"))
    return {r["name"].lower(): r for r in rows}


def spell_data(name: str) -> dict:
    s = _all().get(name.strip().lower())
    if s is None:
        raise ValueError(f"unknown spell {name!r}")
    return s


_SRD524_SPELLS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "srd" / "srd524" / "Spell.json"
)


@functools.lru_cache(maxsize=None)
def _srd524() -> dict:
    rows = json.loads(_SRD524_SPELLS_PATH.read_text(encoding="utf-8"))
    return {
        r["fields"]["name"].lower(): r["fields"]
        for r in rows
        if r.get("fields", {}).get("name")
    }


def srd_spell(name: str):
    """The structured SRD record for any spell (the full ~339-spell srd524 dump),
    or None. Unlike spell_data (the hand-authored spells with full damage/heal
    automation), this returns level / concentration / save ability / damage_roll /
    attack_roll / upcast text for ALL SRD spells — enough for cast_spell to spend
    the right slot, set concentration, and hand the DM the values to resolve with."""
    return _srd524().get(name.strip().lower())


def canonical_name(name: str) -> Optional[str]:
    """The canonical-cased SRD spell name for `name` (case-insensitive), or None if
    `name` is not any of the ~339 SRD spells. Prefers the hand-authored curated record's
    casing, falling back to the srd524 dump — both store the proper name, the lookup tables
    casefold on read. Used by learn_spells/prepare_spells to canonicalize+validate on WRITE
    (F03-7), so the case-sensitive cast gate compares like against like."""
    if not name or not name.strip():
        return None
    key = name.strip().lower()
    rec = _all().get(key) or _srd524().get(key)
    if rec is None:
        return None
    return rec.get("name") or name.strip()


@functools.lru_cache(maxsize=1)
def all_spell_names() -> list[str]:
    """Every castable spell's canonical-cased name (curated + the full srd524 dump),
    de-duped, for did-you-mean fuzzy matching on an unknown-spell refusal (F14-7 / #812).

    Prefers the curated record's casing where both sources carry a spell (curated is the
    hand-authored, full-automation set). Pure + cached — same provenance as srd_spell /
    canonical_name, so a suggestion always names a spell cast_spell can actually resolve."""
    names: dict[str, str] = {}
    for rec in _srd524().values():
        n = rec.get("name")
        if n:
            names[n.lower()] = n
    for rec in _all().values():  # curated casing wins
        n = rec.get("name")
        if n:
            names[n.lower()] = n
    return sorted(names.values())


# --- class spell LISTS (the preparable pool) — #754 ---------------------------
#
# The srd524 dump stamps each spell with a `classes` list of "srd-2024_<class>" slugs
# (e.g. Bless -> ['srd-2024_cleric', 'srd-2024_paladin']). That is the SRD's own
# class↔spell mapping — the set of spells a member of <class> can choose to PREPARE
# (the browsable pool a prepared caster plans from), distinct from the spells the
# character has currently prepared/known. We re-derive the list straight off that
# field so the engine stays the single source of truth (no hand-maintained list to
# drift from the SRD). Pure + cached; additive — a class the dump doesn't know returns [].
_CLASS_SLUG_PREFIX = "srd-2024_"


@functools.lru_cache(maxsize=None)
def _class_spell_index() -> dict[str, list[dict]]:
    """Map each SRD class (lowercase short name: 'paladin', 'wizard', …) to the list of
    that class's spell records (srd524 fields), sorted by (level, name). Built once from
    the `classes` slugs the srd524 dump stamps on every spell. Pure — no campaign state."""
    index: dict[str, list[dict]] = {}
    for rec in _srd524().values():
        for slug in rec.get("classes") or []:
            s = str(slug)
            if not s.startswith(_CLASS_SLUG_PREFIX):
                continue
            cls = s[len(_CLASS_SLUG_PREFIX):].strip().lower()
            if cls:
                index.setdefault(cls, []).append(rec)
    for cls, rows in index.items():
        rows.sort(key=lambda r: (int(r.get("level") or 0), str(r.get("name") or "")))
    return index


def class_spell_list(class_name: str, max_level: Optional[int] = None) -> list[dict]:
    """The full SRD spell list a `class_name` member can PREPARE — the browsable preparable
    pool (#754), NOT what a given character has prepared. Each entry is ``{name, level}``
    (canonical-cased name + integer spell level), sorted by (level, name). Re-derived from
    the srd524 `classes` field, so it's SRD-correct and never hand-maintained.

    `max_level` (when given) caps the list to spells of that spell level or lower — pass a
    caster's highest available SLOT level so the pool only shows spells they can actually
    slot (a L10 Paladin -> levels 1–3). `max_level=0` keeps only cantrips; None (default) =
    the whole class list. Unknown class -> [] (additive: a non-caster shows nothing)."""
    rows = _class_spell_index().get(str(class_name).strip().lower(), [])
    out: list[dict] = []
    for rec in rows:
        lvl = int(rec.get("level") or 0)
        if max_level is not None and lvl > int(max_level):
            continue
        name = rec.get("name")
        if name:
            out.append({"name": str(name), "level": lvl})
    return out


def _parse_dice(expr: str) -> tuple[int, int]:
    """Parse the dice part of 'NdM(+K)' -> (N, M), ignoring any flat modifier."""
    body = expr.lower().split("+")[0].split("-")[0]
    n, _, sides = body.partition("d")
    return int(n or 1), int(sides)


_PLAIN_DICE_RE = re.compile(r"^\s*(\d+)d(\d+)\s*$", re.IGNORECASE)


def cantrip_tier(caster_level: int) -> int:
    """A damage cantrip's dice tier at this caster level: 1 base die, then 2/3/4
    at levels 5/11/17 (the SRD's universal damage-cantrip progression)."""
    lvl = int(caster_level or 1)
    return 1 + (lvl >= 5) + (lvl >= 11) + (lvl >= 17)


def scaled_cantrip_damage(record: dict, caster_level: int) -> Optional[str]:
    """Tier-scaled ``NdM`` damage for a STANDARD srd damage cantrip at this caster
    level, or None when tier scaling does not apply (F03-2 / #808). The srd dump
    stores the LEVEL-1 dice in ``damage_roll``; for the standard "damage increases
    ... at levels 5 (2dM), 11 (3dM), and 17 (4dM)" cantrips the structured value the
    DM is told to roll must be the tier-scaled one. Returns None for:

    * leveled spells (they upcast by slot, not caster level);
    * Eldritch Blast — excluded BY NAME (audit-hardened): it scales in BEAMS, each
      its own 1d10 attack roll, so a pooled "3d10" would misstate the mechanic;
    * cantrips whose ``higher_level`` prose carries no damage increase (Guidance,
      Resistance: the srd dump stores their 1d4 bonus die as a ``damage_roll``,
      but they genuinely never scale — N×tier would be wrong the other way);
    * any non-``NdM`` damage expression (defensive; all 14 are plain today).
    """
    if int(record.get("level") or 0) != 0:
        return None
    m = _PLAIN_DICE_RE.match(str(record.get("damage_roll") or ""))
    if m is None:
        return None
    if str(record.get("name") or "").strip().lower() == "eldritch blast":
        return None
    if "damage increases" not in str(record.get("higher_level") or "").lower():
        return None
    n, sides = int(m.group(1)), int(m.group(2))
    return f"{n * cantrip_tier(caster_level)}d{sides}"


def _scale_per_dart(per: str, darts: int) -> str:
    """'1d4+1' x darts -> '{darts}d4+{darts}'."""
    count, sides = _parse_dice(per)
    plus = int(per.split("+")[1]) if "+" in per else 0
    expr = f"{count * darts}d{sides}"
    total_plus = plus * darts
    if total_plus:
        expr += f"+{total_plus}"
    return expr


def resolve_effect(spell: dict, slot_level: int, caster_level: int, casting_mod: int) -> dict:
    """Compute a cast's mechanical effect. slot_level is the slot used (== spell
    level for cantrips); caster_level scales cantrips; casting_mod is added to
    heals. Returns a dict the DM applies via attack / apply_damage / apply_healing.
    """
    m = spell.get("mechanics", {})
    kind = m.get("kind", "utility")
    spell_level = spell.get("level", 0)
    extra = max(0, slot_level - spell_level)  # upcast steps above the spell's base level

    if kind == "attack":
        dmg = m.get("damage", "1d6")
        scaling = m.get("cantrip_scaling", {})
        for threshold in sorted((int(k) for k in scaling), reverse=True):
            if caster_level >= threshold:
                dmg = scaling[str(threshold)]
                break
        return {"kind": "attack", "damage": dmg, "damage_type": m.get("damage_type", "")}

    if kind == "auto":  # e.g. Magic Missile — auto-hit darts
        darts = m.get("darts", 1) + extra * m.get("upcast_darts_per_level", 0)
        per = m.get("per_dart", "1d4+1")
        return {
            "kind": "auto",
            "darts": darts,
            "per_dart": per,
            "damage": _scale_per_dart(per, darts),
            "damage_type": m.get("damage_type", ""),
        }

    if kind == "heal":
        count, sides = _parse_dice(m.get("heal", "1d8"))
        up = m.get("upcast_dice")
        if up:
            uc, _ = _parse_dice(up)
            count += extra * uc
        expr = f"{count}d{sides}"
        if m.get("add_casting_mod") and casting_mod:
            expr += f"+{casting_mod}" if casting_mod > 0 else str(casting_mod)
        return {"kind": "heal", "heal": expr}

    if kind == "save":
        count, sides = _parse_dice(m.get("damage", "1d6"))
        up = m.get("upcast_dice")
        if up:
            uc, _ = _parse_dice(up)
            count += extra * uc
        return {
            "kind": "save",
            "save_ability": m.get("save_ability", "dex"),
            "damage": f"{count}d{sides}",
            "on_save": m.get("on_save", "half"),
            "damage_type": m.get("damage_type", ""),
        }

    return {"kind": kind, "effect": m.get("effect", "")}  # buff / utility


# --- duration parsing (engine-tracked effect lifetimes) -----------------------
#
# BOTH spell data sources already carry a `duration` field, but in two formats:
#   * the srd524 dump (data/srd/srd524/Spell.json) normalizes it to "<n> <unit>"
#     with a SINGULAR unit — "1 minute", "10 minute", "8 hour", "1 round";
#   * the hand-authored curated set (data/srd/spells.json) uses human prose —
#     "8 hours", "Concentration, up to 1 minute", "Instantaneous".
# `parse_duration` reads either: it strips a leading "Concentration, up to "
# qualifier, then pulls the trailing "<n> <unit>" pair (plural tolerated). Durations
# the engine does NOT count down — instantaneous / until dispelled / special / "" —
# return None (those are resolved-and-done or DM-managed, exactly today's behavior).
#
# Unit mapping (documented once, here):
#   1 round   = 6 seconds                          -> scale "rounds"
#   1 minute  = 10 rounds                           -> scale "minutes" (stored as rounds)
#   1 hour / day                                    -> scale "hours" / "days" (clock-based)
# Combat decrements rounds/minutes per turn; out of combat a single time-of-day
# phase advance (a phase ≫ a minute) expires all minute/round-scale effects, and
# hour/day-scale effects expire when the in-world clock passes their computed
# deadline (and hour-scale also ends on a long rest — see ActiveEffect).
_DURATION_RE = re.compile(r"(\d+)\s*(round|minute|min|hour|hr|day)s?\b", re.IGNORECASE)

# units the engine never counts down (resolved instantly or DM-managed)
_UNTIMED = {"instantaneous", "instant", "until dispelled", "until dispelled or triggered",
            "special", "permanent", ""}

# minutes -> rounds (SRD: a round is 6 seconds, so 1 minute = 10 rounds)
ROUNDS_PER_MINUTE = 10


def parse_duration(duration: Optional[str]) -> Optional[dict]:
    """Normalize a spell's free-text `duration` into a timed-effect descriptor, or
    None when the spell carries no engine-trackable timed duration.

    Returns a dict ``{scale, rounds, hours, days}`` where exactly one of the
    magnitude fields is meaningful for the chosen ``scale``:
      * scale "rounds"  -> ``rounds`` = round count
      * scale "minutes" -> ``rounds`` = minutes * 10 (pre-converted for combat decrement)
      * scale "hours"   -> ``hours``  = hour count
      * scale "days"    -> ``days``   = day count
    """
    if not duration:
        return None
    text = duration.strip().lower()
    if text in _UNTIMED:
        return None
    # Drop a "Concentration, up to ..." (curated) qualifier before matching.
    text = re.sub(r"^concentration,?\s*(up to\s*)?", "", text).strip()
    m = _DURATION_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    if n <= 0:
        return None
    unit = m.group(2)
    if unit in ("round",):
        return {"scale": "rounds", "rounds": n, "hours": 0, "days": 0}
    if unit in ("minute", "min"):
        return {"scale": "minutes", "rounds": n * ROUNDS_PER_MINUTE, "hours": 0, "days": 0}
    if unit in ("hour", "hr"):
        return {"scale": "hours", "rounds": 0, "hours": n, "days": 0}
    if unit in ("day",):
        return {"scale": "days", "rounds": 0, "hours": 0, "days": n}
    return None


# 5e condition names the engine tracks (mirrors models.Condition); used to spot which
# condition a save-ends spell imposes. Kept here (not imported) so spells.py stays a
# pure, I/O-light data helper with no model dependency.
_TRACKED_CONDITIONS = (
    "blinded", "charmed", "deafened", "frightened", "grappled", "incapacitated",
    "invisible", "paralyzed", "petrified", "poisoned", "prone", "restrained",
    "stunned", "unconscious",
)
# The canonical "at the END OF EACH OF ITS TURNS the target repeats the save, ending the
# spell on a single success" clause shared by Hold Person / Hold Monster (and any future
# spell worded identically). Deliberately anchored on the END-OF-TURN trigger: a spell whose
# recurring save fires on a DIFFERENT trigger (Dominate Person/Beast/Monster: "whenever the
# target takes damage") must NOT match — the engine only owns the END-OF-TURN save, so
# auto-imposing it on a damage-triggered spell would roll a save 5e doesn't grant. Likewise
# multi-success (Contagion) and detached-clause (Weird) wordings don't match.
_REPEAT_SAVE_RE = re.compile(
    r"at the end of each of its turns,\s*the target repeats the save,\s*"
    r"ending the spell on itself on a success",
    re.IGNORECASE,
)


def repeat_save_rider(srd_record: Optional[dict]) -> Optional[dict]:
    """For a save-ends condition spell whose victim "repeats the save at the end of each
    of its turns, ending the spell on itself on a success" (Hold Person, Hold Monster),
    return ``{ability, condition}`` — the recurring save's ability and the single condition
    it imposes — so cast_spell can tell the DM exactly how to apply it self-enforcingly
    (#209). Returns None for any other spell.

    GENERAL, not Hold-Person-special: it keys off the canonical SRD wording + a single
    tracked condition, so it fires for Hold Monster too and would fire for a new spell
    worded the same way. Intentionally conservative — a spell with multi-condition or
    multi-success recurring-save semantics returns None (the engine won't guess wrong)."""
    if not srd_record:
        return None
    ability = (srd_record.get("saving_throw_ability") or "").strip().lower()
    desc = (srd_record.get("desc") or "").lower()
    if not ability or not _REPEAT_SAVE_RE.search(desc):
        return None
    found = [cn for cn in _TRACKED_CONDITIONS if re.search(rf"\b{cn}\b", desc)]
    if len(found) != 1:
        return None  # only the unambiguous single-condition case self-enforces
    return {"ability": ability, "condition": found[0]}
