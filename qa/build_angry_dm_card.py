#!/usr/bin/env python3
"""Build the frozen 5e "bench card" the Angry-DM rules-fidelity lens grades against.

Why this exists (design-angry-dm.md §2c): the Angry-DM lens is a single, tool-less
`claude -p` pass like every other lens — it does NOT get the rules MCP at runtime
(that would make it a slow, non-deterministic agentic loop). Instead we PRE-EXTRACT a
compact, frozen reference of the load-bearing rule text into the lens prompt at build
time. The card is GENERATED from the SRD we actually ship
(`data/srd/srd524/Rule.json` + `data/srd/conditions.json`), so it can NEVER drift from
what the engine enforces — a future SRD bump just regenerates it.

What it does:
  1. Reads `data/srd/srd524/Rule.json` (56 finely-grained 2024 SRD-5.2 rules), keeps a
     curated allowlist (~33 names the checklist cites — see ALLOWLIST below / design
     §App. B), trims each `desc` to its first paragraph(s) up to a char budget.
  2. Reads `data/srd/conditions.json` (the 14 conditions with tight effect lines).
  3. Writes the human-readable card to `qa/angry_dm_card.md` (committed, regenerable).
  4. Substitutes the card for the `{{BENCH CARD}}` token in the rubric SOURCE
     (`qa/rubric_angry_dm.src.md`) and writes the final, ready-to-feed
     `qa/rubric_angry_dm.md` — so `qa/score.sh` (which `cat`s the rubric verbatim)
     needs ZERO changes and the lens stays a one-shot.

Usage:  python3 qa/build_angry_dm_card.py
Run from the repo root. Pure stdlib; no deps, no network, no engine import.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULE_JSON = ROOT / "data" / "srd" / "srd524" / "Rule.json"
CONDITIONS_JSON = ROOT / "data" / "srd" / "conditions.json"
CARD_OUT = ROOT / "qa" / "angry_dm_card.md"
RUBRIC_SRC = ROOT / "qa" / "rubric_angry_dm.src.md"
RUBRIC_OUT = ROOT / "qa" / "rubric_angry_dm.md"

BENCH_CARD_TOKEN = "{{BENCH CARD}}"

# Per-rule trim budget. The whole card targets ~6-8 KB (design §2c) so it sits in the
# prompt with budget to spare; ~420 chars/rule * ~33 rules + 14 short conditions lands there.
MAX_CHARS = 420

# The curated allowlist of Rule.json `name` values the Angry-DM checklist cites
# (design §App. B). Apostrophes are written straight here but matched apostrophe-
# INSENSITIVELY against the data (the shipped SRD uses a curly apostrophe in
# "The Bonus Doesn't Stack"), so the card can't silently drop a rule on an SRD bump.
ALLOWLIST = [
    "Ability Checks",
    "Ability Modifiers",
    "Advantage/Disadvantage",
    "The Bonus Doesn't Stack",
    "Armor Class",
    "Attack Rolls",
    "Making an Attack",
    "Melee Attacks",
    "Ranged Attacks",
    "Unseen Attackers and Targets",
    "Cover",
    "Critical Hits",
    "Damage Rolls",
    "Damage Types",
    "Resistance and Vulnerability",
    "Immunity",
    "Extra Attack",
    "The Order of Combat",
    "Movement and Position",
    "Saving Throws",
    "Saving Throws and Damage",
    "Hiding",
    "Vision and Light",
    "Hit Points",
    "Temporary Hit Points",
    "Dropping to 0 Hit Points",
    "Knocking out a Creature",
    "Healing",
    "Resting",
    "Experience Points",
    "Gaining a Level",
    "Spellcasting",
    "Class Features",
]


def _norm(s: str) -> str:
    """Apostrophe/whitespace-insensitive key for matching allowlist names to SRD data.

    The 2024 SRD JSON uses a curly right-single-quote (U+2019) in e.g.
    "The Bonus Doesn't Stack"; we want a straight-apostrophe allowlist entry to match
    it. Fold all single-quote variants to a plain ', collapse whitespace, lowercase.
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _trim_desc(desc: str, budget: int = MAX_CHARS) -> str:
    """Reduce a rule's markdown `desc` to its load-bearing opening.

    The SRD descs are markdown prose: an intro paragraph (or two), THEN `##`
    subheadings that fan out into the verbose sub-rules. We keep only the lead-in
    BEFORE the first subheading, then clamp to `budget` chars at a sentence/word
    boundary so the card stays compact and the lens reads clean sentences.
    """
    text = (desc or "").replace("\r\n", "\n").strip()
    # Drop everything from the first STRUCTURED block onward — the deep sub-rules live
    # under a markdown subheading (`## …`) or a blockquote callout (`> **Round Down**`);
    # keeping only the lead-in prose gives the clean "first ~2 sentences" the card wants.
    cut = re.search(r"\n(?:#{1,6}\s|>\s)", text)
    if cut:
        text = text[: cut.start()]
    # An inline ordered list (`1. …`) is the body of some rules — cut at it ONLY when
    # there's already substantive lead-in prose (else the rule's whole substance IS the
    # list, e.g. "Making an Attack" / "Gaining a Level"; keep it and let the char budget
    # clamp it). 160 chars ≈ a couple of real sentences of intro before the steps begin.
    olist = re.search(r"\n\d+\.\s", text)
    if olist and olist.start() >= 160:
        text = text[: olist.start()]
    # Flatten remaining markdown noise into running prose.
    text = re.sub(r"\n{2,}", " ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if len(text) <= budget:
        return text
    # Over budget: clamp at the last sentence end within budget, else the last word.
    head = text[:budget]
    m = list(re.finditer(r"[.!?](?:\s|$)", head))
    if m:
        return head[: m[-1].end()].strip()
    sp = head.rfind(" ")
    return (head[:sp] if sp > 0 else head).rstrip() + "…"


def build_card() -> str:
    rules_raw = json.loads(RULE_JSON.read_text(encoding="utf-8"))

    # name(normalized) -> {display name, trimmed desc}. First occurrence wins
    # (Rule.json has a few duplicate names across rulesets; the first is the canonical
    # combat/d20 entry we want).
    by_norm: dict[str, dict[str, str]] = {}
    for entry in rules_raw:
        fields = entry.get("fields", {})
        name = fields.get("name")
        if not name:
            continue
        key = _norm(name)
        if key in by_norm:
            continue
        by_norm[key] = {"name": name, "desc": _trim_desc(fields.get("desc", ""))}

    lines: list[str] = []
    lines.append("### Rules (from `data/srd/srd524/Rule.json`, SRD 5.2.1 — AUTHORITATIVE)")
    lines.append("")
    missing: list[str] = []
    for want in ALLOWLIST:
        got = by_norm.get(_norm(want))
        if not got or not got["desc"]:
            missing.append(want)
            continue
        lines.append(f"- **{got['name']}** — {got['desc']}")
    lines.append("")

    # Conditions: the 14 with their tight one-line effects (design §App. B).
    conditions = json.loads(CONDITIONS_JSON.read_text(encoding="utf-8"))
    lines.append("### Conditions (from `data/srd/conditions.json`, all 14 — effects are AUTHORITATIVE)")
    lines.append("")
    for cond in conditions:
        nm = cond.get("name", "").strip()
        eff = _trim_desc(cond.get("description", ""), budget=320)
        if nm and eff:
            lines.append(f"- **{nm}** — {eff}")
    lines.append("")

    if missing:
        # Loud, non-fatal: an allowlist name no longer in the SRD means a future bump
        # renamed/removed it. The card is still emitted; the operator regenerates the
        # allowlist. We do NOT silently drop coverage without saying so.
        print(
            f"[build_angry_dm_card] WARNING: {len(missing)} allowlist rule(s) not found "
            f"in Rule.json (apostrophe-insensitive): {missing}. "
            f"Card emitted WITHOUT them — update ALLOWLIST after an SRD bump.",
            file=sys.stderr,
        )

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    for required in (RULE_JSON, CONDITIONS_JSON, RUBRIC_SRC):
        if not required.exists():
            print(f"[build_angry_dm_card] ERROR: missing input {required}", file=sys.stderr)
            return 2

    card = build_card()
    CARD_OUT.write_text(card, encoding="utf-8")

    # Inline the card into the rubric SOURCE -> the final rubric `score.sh` feeds.
    src = RUBRIC_SRC.read_text(encoding="utf-8")
    if BENCH_CARD_TOKEN not in src:
        print(
            f"[build_angry_dm_card] ERROR: token {BENCH_CARD_TOKEN!r} not found in {RUBRIC_SRC}",
            file=sys.stderr,
        )
        return 2
    # Strip the source-only banner comment (the "<!-- SOURCE FILE … -->" at the top) so
    # the GENERATED rubric — which IS the file score.sh feeds — doesn't carry a
    # contradictory "do NOT feed this" note. Replace it with a short generated-by header.
    src = re.sub(
        r"\A(# .*\n\n)<!--.*?-->\n*",
        r"\1<!-- GENERATED by qa/build_angry_dm_card.py from qa/rubric_angry_dm.src.md "
        r"+ the SRD bench card. Do NOT hand-edit: edit the .src.md and rerun the build. -->\n\n",
        src,
        count=1,
        flags=re.DOTALL,
    )
    rubric = src.replace(BENCH_CARD_TOKEN, card.rstrip())
    RUBRIC_OUT.write_text(rubric, encoding="utf-8")

    rule_count = card.count("\n- **")  # rules + conditions bullets
    print(
        f"[build_angry_dm_card] wrote {CARD_OUT.relative_to(ROOT)} "
        f"({len(card)} bytes, {rule_count} entries) and "
        f"{RUBRIC_OUT.relative_to(ROOT)} ({len(rubric)} bytes)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
