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
