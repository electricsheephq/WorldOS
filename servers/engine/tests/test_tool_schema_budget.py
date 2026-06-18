"""SYN-02 — tool-schema slab budget guard, now TIER-AWARE (slab decision, Phase 2).

The engine tool-schema slab is injected via the MCP tool list into EVERY DM request. Under the
tiered config (slab decision) only the census-backed ``server.PINNED_ALLOWLIST`` core carries
``_meta['anthropic/alwaysLoad']`` and stays in-context every beat; the cold tail is deferred behind
the harness ToolSearch. This guard pins that win and keeps it from regressing:

  1. The PINNED-core slab (what ships every beat in the tiered arm) stays under a RATCHET ceiling.
  2. The pinned set is EXACTLY ``PINNED_ALLOWLIST`` — so a NEW @mcp.tool defaults to DEFERRED and a
     careless promotion into the core is a visible, reviewed diff (the forcing function).
  3. Per-tool ``_meta['anthropic/alwaysLoad']`` actually propagates to ``list_tools()`` — a
     FastMCP/claude upgrade that broke the path would silently un-pin the hot set; fail loud instead.
  4. The full ``list_tools()`` JSON (the baseline arm still ships all tools) stays under a secondary
     cap, and the 18 core "reach-for" tools keep their disambiguating first sentence.

Baselines (measure with qa/schema_mass.py): full slab (all 156) ~124,835 B; pinning the
census-backed core keeps the per-beat tiered slab to ~66,955 B. Lower a ratchet when
tiering/trimming reclaims more; raise it ONLY with a justification (a tool promoted into the core).
Rebase over #1246/#1248/#1250 added 3 grid-combat verbs (set_grid, place_combatant_at_coords,
move_to_coords) — grid twins of the already-pinned zone verbs — promoted into the core, which lifts
both the pinned ratchet (63,862 -> 66,955 B) and the full-slab cap (118,739 -> 124,835 B).
"""

import asyncio
import contextlib
import json

import server


# Ratchet ceiling for the PINNED core (the slab injected every beat in the tiered arm).
PINNED_SLAB_BUDGET_BYTES = 67_000
# Secondary cap on the whole list_tools() JSON (the baseline arm still ships all 156 tools).
FULL_SLAB_BUDGET_BYTES = 125_000

# Core "reach-for" tools whose first descriptive sentence the DM relies on to disambiguate.
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
    return list(asyncio.run(server.mcp.list_tools()))


def _bytes(tools) -> int:
    blob = json.dumps(
        [t.model_dump(by_alias=True, exclude_none=True) for t in tools],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return len(blob.encode("utf-8"))


def _alwaysload(tool) -> bool:
    meta = tool.model_dump(by_alias=True, exclude_none=True).get("_meta") or {}
    return meta.get("anthropic/alwaysLoad") is True


@contextlib.contextmanager
def _tiered():
    """Apply per-tool tiering, yield the wire tools, then restore the byte-clean baseline so sibling
    tests (and the full-slab assertion) see a registry with no leaked ``_meta``."""
    server._apply_tool_tiering(force=True)
    try:
        yield _wire_tools()
    finally:
        for t in server.mcp._tool_manager._tools.values():
            if t.meta:
                t.meta.pop(server._ALWAYS_LOAD_META_KEY, None)
                if not t.meta:
                    t.meta = None


def test_pinned_slab_under_ratchet():
    """The PINNED core — the slab actually injected into every DM beat under tiering — stays under
    the ratchet ceiling (and per-tool _meta propagates so the pin is real)."""
    with _tiered() as tools:
        pinned = [t for t in tools if t.name in server.PINNED_ALLOWLIST]
        size = _bytes(pinned)
        assert size <= PINNED_SLAB_BUDGET_BYTES, (
            f"pinned tool slab is {size} B, over the {PINNED_SLAB_BUDGET_BYTES} B ratchet "
            f"({len(pinned)} pinned tools). Either a pinned description grew (trim its prose, keep "
            f"the reach-for first sentence + args) or a tool was promoted into PINNED_ALLOWLIST — "
            f"in which case lower/raise this ratchet WITH a justification."
        )
        # Per-tool alwaysLoad must actually reach the wire, else the harness silently un-pins the
        # hot set (a FastMCP/claude upgrade could change the path).
        pb = next(t for t in tools if t.name == "persist_beat")
        assert _alwaysload(pb), (
            "per-tool _meta['anthropic/alwaysLoad'] did NOT propagate to list_tools() — the "
            "FastMCP/claude per-tool pinning path changed; refusing to silently ship an un-pinned slab"
        )


def test_pinned_set_is_exactly_the_allowlist():
    """The pinned set == the reviewed PINNED_ALLOWLIST, so a NEW @mcp.tool defaults to DEFERRED and a
    promotion into the always-loaded core is a visible, reviewed change (the growth forcing-function)."""
    with _tiered() as tools:
        pinned = {t.name for t in tools if _alwaysload(t)}
        allow = set(server.PINNED_ALLOWLIST)
        assert pinned == allow, (
            "pinned set != PINNED_ALLOWLIST. "
            f"unexpectedly pinned: {sorted(pinned - allow)}; "
            f"in allowlist but not pinned: {sorted(allow - pinned)}. "
            "New tools must default DEFERRED; update PINNED_ALLOWLIST deliberately if a tool is "
            "genuinely hot/cold-open/combat-path."
        )


def test_baseline_is_byte_clean():
    """The whole-server baseline arm (WORLDOS_ENGINE_ALWAYSLOAD default) carries NO per-tool _meta —
    so adopting tiering is byte-identical until the production default is flipped (post-A/B cutover)."""
    tools = _wire_tools()
    assert not any(_alwaysload(t) for t in tools), (
        "baseline list_tools() carries per-tool alwaysLoad _meta — the import-time apply should be "
        "inert under the whole-server baseline (it would bloat the baseline slab)."
    )


def test_full_list_tools_under_secondary_budget():
    """The full list_tools() JSON (baseline arm ships all 153) stays under a secondary cap — a
    backstop against unbounded total schema growth independent of which tools are pinned."""
    size = _bytes(_wire_tools())
    assert size <= FULL_SLAB_BUDGET_BYTES, (
        f"full list_tools JSON is {size} B, over the {FULL_SLAB_BUDGET_BYTES} B secondary cap. "
        f"Trim verbose descriptions (keep the reach-for first sentence + args; cut prose/examples)."
    )


def test_reach_for_first_sentence_intact():
    by_name = {t.name: t for t in _wire_tools()}
    missing = sorted(REACH_FOR_TOOLS - set(by_name))
    assert not missing, f"reach-for tools vanished from list_tools: {missing}"
    # Every reach-for tool must also be PINNED (the DM reaches for it directly — never defer it).
    not_pinned = sorted(REACH_FOR_TOOLS - set(server.PINNED_ALLOWLIST))
    assert not not_pinned, (
        f"reach-for tools are not in PINNED_ALLOWLIST (would be deferred): {not_pinned}"
    )
    thin = []
    for name in sorted(REACH_FOR_TOOLS):
        desc = (by_name[name].description or "").strip()
        if len(desc) < 25 or len(desc.split()) < 4:
            thin.append((name, desc[:60]))
    assert not thin, (
        "reach-for first sentence was over-trimmed (the diet amputated intent the DM "
        f"needs to pick the tool): {thin}"
    )
