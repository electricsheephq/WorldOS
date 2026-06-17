#!/usr/bin/env python3
"""Transcode OpenClaw agent session-transcript JSONL -> Anthropic `claude -p`
stream-json, so qa/distill.py + qa/assert_behavioral.py (written for the
`claude -p --output-format stream-json` shape) run UNCHANGED over an OpenClaw run.

Reads OpenClaw session lines on STDIN (the NEW lines of <agent>/sessions/<sid>.jsonl
for one turn), writes the equivalent Anthropic stream-json lines on STDOUT.

OpenClaw session line (type=="message"):
    {"type":"message","message":{"role":"assistant","content":[ <block> ]}}
  blocks seen:
    {"type":"toolCall","name":"worldos-engine.advance_time","input":{…},"arguments":…,"id":…}
    {"type":"toolResult","name":"worldos-engine.advance_time","text":"<result json>",…}
    {"type":"text","text":"…"}
  (the role may be "assistant" | "toolResult"; the content may also be a bare string.)

Anthropic stream-json this emits (what distill.py + the gate's _tally read):
    assistant text   -> {"type":"assistant","message":{"content":[{"type":"text","text":…}]}}
    tool call        -> {"type":"assistant","message":{"content":[{"type":"tool_use","name":"server__tool","input":{…}}]}}
    tool result      -> {"type":"user","message":{"content":[{"type":"tool_result","content":"<text>"}]}}

CRITICAL: the gate tallies tool names via `name.split("__")[-1]`. OpenClaw names them
`server.tool` (dot), so we rewrite the FIRST dot to "__" (e.g. worldos-engine.attack ->
worldos-engine__attack) so `.split("__")[-1]` == "attack". Tools with no dot pass through.
"""
from __future__ import annotations

import json
import sys


def _to_anthropic_name(name: str) -> str:
    """worldos-engine.attack -> worldos-engine__attack ; bare 'attack' -> 'attack'."""
    if not name:
        return name
    # OpenClaw uses one "server.tool" dot; convert it to the Anthropic "__" join so the
    # gate's name.split("__")[-1] recovers the bare tool name. Split on the FIRST dot only
    # (server ids don't contain dots here; tool names never do).
    if "." in name:
        server, _, tool = name.partition(".")
        return f"{server}__{tool}"
    return name


def _blocks(msg: dict):
    """A message's content is a list of typed blocks, or (rarely) a bare string."""
    content = msg.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + "\n")


def _assistant_text(text: str) -> None:
    _emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


def _assistant_tool_use(name: str, inp) -> None:
    _emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": _to_anthropic_name(name), "input": inp if isinstance(inp, dict) else {}}
    ]}})


def _user_tool_result(text: str) -> None:
    _emit({"type": "user", "message": {"content": [{"type": "tool_result", "content": text}]}})


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # Only the conversation "message" records carry blocks; skip "session"/meta lines.
        if ev.get("type") != "message":
            continue
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else ev
        for b in _blocks(msg):
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                t = (b.get("text") or "").strip()
                if t:
                    _assistant_text(t)
            elif bt in ("toolCall", "tool_use", "tool_call"):
                name = b.get("name") or b.get("toolName") or ""
                # OpenClaw carries args under "input" (parsed) and/or "arguments" (raw string).
                inp = b.get("input")
                if not isinstance(inp, dict):
                    args = b.get("arguments")
                    if isinstance(args, dict):
                        inp = args
                    elif isinstance(args, str):
                        try:
                            inp = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            inp = {"_raw": args}
                    else:
                        inp = {}
                _assistant_tool_use(name, inp)
            elif bt in ("toolResult", "tool_result"):
                # The result content is in "text" (a JSON string) or "content".
                res = b.get("text")
                if res is None:
                    c = b.get("content")
                    if isinstance(c, list):
                        res = " ".join(
                            (x.get("text", "") if isinstance(x, dict) else str(x)) for x in c
                        )
                    else:
                        res = c if isinstance(c, str) else json.dumps(c, default=str)
                _user_tool_result(res if isinstance(res, str) else json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
