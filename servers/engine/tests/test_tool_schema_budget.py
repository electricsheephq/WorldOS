"""SYN-02 (F13-1 + F14-6): the engine tool-schema slab is pinned via ``alwaysLoad``
into EVERY DM request — it is ~54% of the lean first-request floor and the #1
latency line item against #753. This test is the CI byte-budget guard that keeps the
docstring diet from regressing: a careless verbose-docstring re-add re-inflates the
pinned mass and silently re-taxes every beat.

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (SYN-02 / F13-1 / F14-6).

Two assertions, both load-bearing:
  1. The serialized ``list_tools()`` JSON (what the wrapper pins) stays under budget.
  2. The "reach-for" first sentence of a sample of CORE tools survives the diet — the
     diet must compress prose/examples, NEVER amputate the one sentence the DM needs to
     pick the right tool (the reach-for lesson: a wrongly-blanked tool the DM needs is
     worse than the token cost).
"""

import asyncio
import json

import server


# Authoritative metric: the exact wire shape the harness pins (name + description +
# inputSchema), serialized compactly. Baseline was 167,989 B BEFORE the docstring diet
# and 105,483 B AFTER (−37%). The budget sits with headroom above the post-diet size so
# normal tool additions don't trip it, but a wholesale verbose-prose re-add (the
# regression we're guarding — it silently re-taxes every pinned DM request) does.
# Re-measure with qa/schema_mass.py and only raise this number with a justification.
#
# HV4 (#1326, 2026-07-07): raised 120_000 -> 122_000. The prior budget had only ~6 B of
# headroom, so the ONE new reuse tool `lookup_library` (a terse ~730 B schema — the read-only
# assembly mirror of lookup_lore) needed a deliberate, justified bump. 122_000 restores ~1.3 KB
# of headroom above the post-HV4 size (120,724 B) without re-opening the door to a verbose re-add.
SCHEMA_JSON_BUDGET_BYTES = 122_000

# Core "reach-for" tools whose first descriptive sentence the DM relies on to disambiguate.
# Each must keep a non-trivial leading sentence after the diet.
REACH_FOR_TOOLS = {
    "start_world",
    "look_around",
    "travel_to",
    "skill_check",
    "cast_spell",
    "attack",
    "start_combat",
    "log_event",
    "remember",
    "scene_context",
    "persist_beat",
    "create_character",
    "load_canon_character",
    "generate_image",
    "recruit_companion",
    "long_rest",
    "short_rest",
    "advance_time",
}


def _wire_tools():
    tools = asyncio.run(server.mcp.list_tools())
    return [t.model_dump(exclude_none=True) if hasattr(t, "model_dump") else dict(t) for t in tools]


def test_list_tools_json_under_budget():
    wire = _wire_tools()
    blob = json.dumps(wire, ensure_ascii=False, separators=(",", ":"))
    size = len(blob.encode("utf-8"))
    assert size <= SCHEMA_JSON_BUDGET_BYTES, (
        f"engine list_tools JSON is {size} B, over the {SCHEMA_JSON_BUDGET_BYTES} B "
        f"pinned-schema budget (SYN-02). The docstring diet regressed — compress the "
        f"verbose docstrings (keep the first reach-for sentence + args; cut prose/examples)."
    )


def test_reach_for_first_sentence_intact():
    by_name = {t["name"]: t for t in _wire_tools()}
    missing = sorted(REACH_FOR_TOOLS - set(by_name))
    assert not missing, f"reach-for tools vanished from list_tools: {missing}"
    thin = []
    for name in sorted(REACH_FOR_TOOLS):
        desc = (by_name[name].get("description") or "").strip()
        # The reach-for sentence must survive — guard against a blanked / amputated
        # description (a couple of words is not enough to disambiguate the tool).
        if len(desc) < 25 or len(desc.split()) < 4:
            thin.append((name, desc[:60]))
    assert not thin, (
        "reach-for first sentence was over-trimmed (the diet amputated intent the DM "
        f"needs to pick the tool): {thin}"
    )
