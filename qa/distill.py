#!/usr/bin/env python3
"""Distill a `claude -p --output-format stream-json` transcript into a readable
play log + a tool-call tally.

Usage: python qa/distill.py qa/transcripts/play1.jsonl
Writes <input>.md next to it and prints a tool-call summary to stdout.

Tolerant of event-shape variation: it dispatches on each line's "type" and
falls back to noting unknown events rather than crashing, so it survives minor
stream-json format drift.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def _short(v, n=220) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _multiattack_rejection_line(text: str) -> str | None:
    """Render a one-line audit note when ``text`` is the engine's attack-budget rejection
    (the per-action ceiling that enforces a monster's stat-block Multiattack or a PC's
    Extra-Attack/Action-Surge limit). Matches the exact engine phrasings from
    combat.check_action_attack so ordinary error prose never trips it. Returns None when the
    text is not such a rejection."""
    import re as _re

    m = _re.search(
        r"(this creature's Multiattack grants \d+ attack\(s\) per turn[^\"']*)",
        text,
        _re.IGNORECASE,
    )
    if m is None:
        m = _re.search(
            r"((?:no attacks left this turn|already attacked this turn)[^\"']*)",
            text,
            _re.IGNORECASE,
        )
    if m is None:
        return None
    reason = " ".join(m.group(1).split()).rstrip(".")
    return f"    `↳ attack-rejected: {reason}`"


def _audit_fields(res) -> list[str]:
    """Surface engine-auto-fired mechanics that the 240-char tool_result preview truncates,
    so the scorer can AUDIT them tool-sourced rather than only in DM prose (a recurring
    Angry-DM finding). Currently: next_turn's ``repeat_saves`` (Hold Person/Monster
    end-of-turn escape saves, #209), attack/use_resource ``maneuver_damage`` (the Battle
    Master superiority die, #213), and attack()'s Multiattack/Extra-Attack BUDGET — both the
    ``attacks_made/allowed_this_turn`` ceiling on a swing AND the engine's REJECTION of an
    over-budget attack (F01-1 / csmed-4: the Ghoul's two-Bite Multiattack ceiling IS enforced
    in the engine, but the 585-char attack result truncates at 240 so the scorer never sees
    the budget — it then reads a DM that 'conjured a Multiattack that doesn't exist'). The
    rejection arrives as either a plain exception string or an ``{"error": …}`` envelope.
    Best-effort: a non-JSON, non-dict, irrelevant result yields nothing (the normal truncated
    `← …` preview line still stands)."""
    # A plain-string is_error tool_result (the MCP layer renders the exception text directly,
    # not JSON) — surface a Multiattack/attack-budget REJECTION before the JSON parse, since a
    # bare string would otherwise json.loads-fail straight to []. Only fires on the engine's
    # exact ceiling phrasing so ordinary error prose stays untouched.
    if isinstance(res, str):
        try:
            json.loads(res)  # is it actually JSON? if so, fall through to the dict path
            is_plain = False
        except (json.JSONDecodeError, ValueError):
            is_plain = True
        if is_plain:
            line = _multiattack_rejection_line(res)
            return [line] if line else []

    try:
        data = json.loads(res) if isinstance(res, str) else res
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    # A JSON-enveloped error ({"error": "…cannot attack: …Multiattack grants N…"}).
    err = data.get("error") or data.get("detail")
    if isinstance(err, str):
        line = _multiattack_rejection_line(err)
        if line:
            out.append(line)
    # A resolved attack swing: surface the per-turn attack BUDGET when it is > 1 (a real
    # Multiattack or Extra-Attack ceiling). Single-attack swings (allowed == 1, the 95% case)
    # surface nothing — no noise, mirroring repeat_saves/maneuver_damage firing only when
    # there is a mechanic to audit. This is the tool-sourced proof the engine constrained the
    # monster to its stat-block Multiattack (csmed-4): "Ghoul 1/2 attacks this turn".
    allowed = data.get("attacks_allowed_this_turn")
    made = data.get("attacks_made_this_turn")
    if isinstance(allowed, int) and allowed > 1 and isinstance(made, int):
        # Label the budget SOURCE from engine-surfaced fields — never guess (cs-timing F-2).
        #   * ``multiattack_grants`` (int) → a monster's stat-block Multiattack.
        #   * else a PC: the engine surfaces ``extra_attacks`` / ``surge_actions`` so distill
        #     distinguishes the Extra Attack FEATURE from an Action-Surge second action. A
        #     Fighter L4 (Extra Attack is L5: extra_attacks=0) who spent Action Surge MUST read
        #     "(Action Surge)", never "(Extra Attack)" — the feature it does not yet have.
        if isinstance(data.get("multiattack_grants"), int):
            kind = "Multiattack"
        else:
            parts = []
            if isinstance(data.get("extra_attacks"), int) and data["extra_attacks"] > 0:
                parts.append("Extra Attack")
            if isinstance(data.get("surge_actions"), int) and data["surge_actions"] > 0:
                parts.append("Action Surge")
            # Fallback only when the engine surfaced no source (legacy/synthetic data); a
            # KNOWN extra_attacks=0 never falls here, so it can never be mislabeled "Extra Attack".
            kind = " + ".join(parts) if parts else "Extra Attack"
        actor = data.get("attacker", "the attacker")  # always present on an attack() return
        out.append(
            f"    `↳ attack-budget: {actor} {made}/{allowed} attacks this turn ({kind})`"
        )
    for rs in data.get("repeat_saves", []) or []:
        if not isinstance(rs, dict):
            continue
        verdict = "ENDS" if rs.get("ended") else ("saved" if rs.get("success") else "held")
        out.append(
            f"    `↳ repeat-save: {rs.get('name', '?')} on {rs.get('character_id', '?')} — "
            f"{str(rs.get('ability', '?')).upper()} {rs.get('roll', '?')} (nat {rs.get('natural', '?')}) "
            f"vs DC {rs.get('dc', '?')} → {verdict}`"
        )
    md = data.get("maneuver_damage")
    if isinstance(md, dict):
        # On a CRIT the superiority die doubles (SRD Critical Hits, #213/A): show that the
        # doubling WAS applied (rolled is the doubled total) so the Angry-DM lens reads the
        # crit-doubled die as tool-sourced, not a hallucinated number.
        crit_tag = ""
        if md.get("crit_doubled"):
            crit_tag = (
                f" CRIT×2 ({md.get('base_rolled', '?')}+{md.get('crit_extra', '?')})"
            )
        out.append(
            f"    `↳ maneuver-damage: {md.get('maneuver', '?')} {md.get('die', '?')}="
            f"{md.get('rolled', '?')}{crit_tag} applied={md.get('applied', md.get('applies_to', '?'))}`"
        )
    # #792 auto-concentration: attack/apply_damage auto-roll the CON save when a
    # concentrating creature takes damage. The result sits AFTER target_state in the JSON,
    # so the 240-char preview truncates it and the Angry-DM lens mis-reads a tool-sourced
    # save as a hallucinated "10 vs 10" prose number. Surface it so the lens can audit it.
    cs = data.get("concentration_save")
    if isinstance(cs, dict):
        verdict = "MAINTAINED" if cs.get("maintained") else "BROKEN"
        spell = cs.get("spell")
        out.append(
            f"    `↳ concentration-save: {cs.get('target', cs.get('character_id', '?'))} "
            f"{str(cs.get('ability', 'con')).upper()} {cs.get('roll', '?')} "
            f"(nat {cs.get('natural', '?')}) vs DC {cs.get('dc', '?')} → {verdict}"
            + (f" ({spell})" if spell else "")
            + "`"
        )
    # #792/F3-6: when concentration breaks, the held victims (Hold Person/Monster) are freed.
    freed = data.get("freed_targets")
    if isinstance(freed, list) and freed:
        out.append(
            f"    `↳ freed-on-concentration-end: "
            f"{', '.join(str(f) for f in freed if f)}`"
        )
    # #792: a deferred on-hit advantage rider (e.g. Guiding Bolt) consumed by the next attack.
    adv = data.get("advantage_consumed")
    if adv:
        src = adv if isinstance(adv, str) else (
            adv.get("source") if isinstance(adv, dict) else None
        )
        out.append(f"    `↳ advantage-consumed{f': {src}' if src else ''}`")
    return out


def _content_blocks(msg: dict):
    """A message's content may be a string or a list of typed blocks."""
    content = msg.get("content", [])
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def distill(path: Path) -> tuple[str, Counter]:
    lines_out: list[str] = []
    tools: Counter = Counter()
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type")
        if etype == "assistant":
            for b in _content_blocks(ev.get("message", {})):
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    lines_out.append(f"\n**DM/Player:** {b['text'].strip()}")
                elif bt == "tool_use":
                    name = b.get("name", "?")
                    tools[name] += 1
                    args = b.get("input", {})
                    lines_out.append(f"  - `→ {name}({_short(args, 160)})`")
        elif etype == "user":
            for b in _content_blocks(ev.get("message", {})):
                if b.get("type") == "tool_result":
                    res = b.get("content", "")
                    if isinstance(res, list):
                        res = " ".join(
                            x.get("text", "") for x in res if isinstance(x, dict)
                        )
                    lines_out.append(f"    `← {_short(res, 240)}`")
                    lines_out.extend(_audit_fields(res))
        elif etype == "result":
            cost = ev.get("total_cost_usd", "?")
            turns = ev.get("num_turns", "?")
            dur = ev.get("duration_ms", "?")
            lines_out.append(
                f"\n---\n_run: cost=${cost} turns={turns} duration_ms={dur} "
                f"is_error={ev.get('is_error')}_"
            )
    return "\n".join(lines_out), tools


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: distill.py <transcript.jsonl>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    body, tools = distill(path)
    out = path.with_suffix(".md")
    tally = "\n".join(f"  {n}: {c}" for n, c in tools.most_common())
    header = f"# Playtest transcript: {path.name}\n\n## Tool-call tally\n{tally}\n\n## Play log\n"
    out.write_text(header + body + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print("tool-call tally:")
    print(tally or "  (no tool calls detected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
