#!/usr/bin/env python3
"""SYN-02 (F13-1 / F14-6) measurement tool — dump the engine MCP tool-schema mass.

The engine's tool schemas are pinned into EVERY DM request via ``alwaysLoad`` — the #1
latency line item against #753 (~54% of the lean first-request floor). This script dumps
the authoritative size of what gets pinned: the serialized ``list_tools()`` JSON
(name + description + inputSchema), so a before/after token/byte delta can be reported
honestly and the CI byte-budget guard (servers/engine/tests/test_tool_schema_budget.py)
has a matching manual probe.

Run:
    uv run --directory servers/engine python ../../qa/schema_mass.py
    # or, with the engine dir on PYTHONPATH:
    CLAWDND_STATE_DIR=/tmp/x uv run --directory servers/engine python <abs path>/qa/schema_mass.py

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (SYN-02 / F13-1 / F14-6).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile


def _load_engine_server():
    # Allow running from the repo root or the engine dir.
    here = os.path.dirname(os.path.abspath(__file__))
    engine = os.path.join(here, "..", "servers", "engine")
    if os.path.isdir(engine):
        sys.path.insert(0, os.path.abspath(engine))
    os.environ.setdefault("CLAWDND_STATE_DIR", tempfile.mkdtemp(prefix="schema_mass_"))
    import server  # noqa: E402  (import after sys.path / env are set)

    return server


async def _measure(server):
    tools = await server.mcp.list_tools()
    wire = []
    desc_chars = 0
    longest = []
    for t in tools:
        d = t.model_dump(exclude_none=True) if hasattr(t, "model_dump") else dict(t)
        wire.append(d)
        dc = len(d.get("description") or "")
        desc_chars += dc
        longest.append((dc, d["name"]))
    blob = json.dumps(wire, ensure_ascii=False, separators=(",", ":"))
    by = len(blob.encode("utf-8"))
    longest.sort(reverse=True)
    return {
        "tools": len(tools),
        "list_tools_json_bytes": by,
        "approx_tokens": by // 4,  # ~4 chars/token, order-of-magnitude only
        "description_chars_total": desc_chars,
        "top10_longest_descriptions": longest[:10],
    }


def main() -> int:
    server = _load_engine_server()
    out = asyncio.run(_measure(server))
    print(f"engine tools                : {out['tools']}")
    print(f"list_tools JSON bytes       : {out['list_tools_json_bytes']:,}")
    print(f"approx tokens (~4 ch/tok)   : {out['approx_tokens']:,}")
    print(f"description chars total     : {out['description_chars_total']:,}")
    print("top-10 longest descriptions :")
    for dc, name in out["top10_longest_descriptions"]:
        print(f"    {dc:5d}c  {name}")
    # Machine-readable tail for scripting / before-after diffs.
    print("JSON " + json.dumps({k: v for k, v in out.items() if k != "top10_longest_descriptions"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
