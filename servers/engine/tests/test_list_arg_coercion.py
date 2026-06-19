"""LIST-ARG COERCION: a string / comma-string passed where a list is expected is coerced
to a list at the Pydantic VALIDATION layer, instead of tripping the FATAL
``no_rejected_tool_calls`` behavioral gate and capping every lens.

THE BUG (model-AGNOSTIC, proven on Claude baseline-rc1 + cue-thaw AND GLM): FastMCP
validates a tool's args against the function type hints with Pydantic BEFORE the function
body runs. When the DM model passes ``approval_tags="honest_dealing"`` (a bare string) or
``actor_ids="id1,id2"`` (a comma-string) where a ``list`` is expected, Pydantic raised
``Input should be a valid list [type=list_type, ...]`` -> the rejected tool call flipped
the release-gate ``no_rejected_tool_calls`` FATAL check RED -> all three lenses capped to
<= 2.5 on an otherwise-coherent session (the ~30%-of-runs false-cap).

THE FIX: a Pydantic ``BeforeValidator`` (models._coerce_list, exposed as the ListArg /
ReqListArg / StrListArg / OptStrListArg aliases) coerces a string -> list BEFORE the
list-type check, on the high-traffic DM-called list args. It is ADDITIVE — a real list
and ``None`` pass through untouched, and a genuinely-wrong type (int/dict) is STILL
rejected loudly (we never silently swallow a real type bug). It is wire-NEUTRAL — a
BeforeValidator is invisible to ``json_schema()``, so the param stays a plain ``array``
and the pinned-schema byte budget (test_tool_schema_budget) does not regress.

These tests exercise the REAL rejection surface — the MCP tool manager, which builds +
validates the auto-generated pydantic args model — NOT a direct ``server.fn(...)`` kwargs
call (that bypasses the validator entirely). So they would RED pre-fix on the string cases
and they prove the wrong-type cases still reject.
"""

import asyncio

import pytest

import server
from models import (
    ListArg,
    OptStrListArg,
    ReqListArg,
    StrListArg,
    _coerce_list,
)
from pydantic import BeforeValidator, TypeAdapter


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign("Coerce")["id"]


def _call(name: str, args: dict):
    """Invoke a registered MCP tool through the tool manager — the path the DM agent uses,
    which builds + validates the auto-generated pydantic args model. This is the EXACT layer
    that raised the ``list_type`` rejection pre-fix (a direct server.fn(...) call bypasses it)."""
    return asyncio.run(server.mcp._tool_manager.call_tool(name, args))


def _struct(res):
    """The tool manager returns a ``(content, structured)`` shape across mcp versions; pull the
    structured result dict the tool actually returns."""
    if isinstance(res, tuple):
        for part in res:
            if isinstance(part, dict):
                return part.get("result", part)
        return res[-1]
    return res


# ---------------------------------------------------------------------------------------------
# 1. The pure helper / alias behavior (the unit-level contract every alias shares).
# ---------------------------------------------------------------------------------------------

def test_coerce_list_helper_contract():
    # string -> single-element list
    assert _coerce_list("honest_dealing") == ["honest_dealing"]
    # comma-string -> split + per-token strip + blank-drop
    assert _coerce_list("id1,id2") == ["id1", "id2"]
    assert _coerce_list(" id1 , id2 ,") == ["id1", "id2"]
    # empty / whitespace string -> []
    assert _coerce_list("") == []
    assert _coerce_list("   ") == []
    # a real list passes through UNCHANGED (correct caller's path)
    real = ["a", "b"]
    assert _coerce_list(real) == ["a", "b"]
    # None passes through (the omitted / default case)
    assert _coerce_list(None) is None
    # a genuinely-wrong type is returned AS-IS so Pydantic still rejects it loudly
    assert _coerce_list(5) == 5
    assert _coerce_list({"a": 1}) == {"a": 1}


@pytest.mark.parametrize("alias", [ListArg, ReqListArg, StrListArg, OptStrListArg])
def test_alias_wire_schema_is_plain_array(alias):
    """Each coercing alias emits the SAME json_schema as its un-annotated base type — a
    BeforeValidator is invisible to the wire schema, so the pinned byte budget can't regress."""
    coerced = TypeAdapter(alias).json_schema()
    # strip the BeforeValidator and re-derive the base schema
    base_type = alias.__args__[0]
    plain = TypeAdapter(base_type).json_schema()
    assert coerced == plain
    # and the emitted shape is an array (anyOf for the Optional variants)
    assert "array" in str(coerced)


# ---------------------------------------------------------------------------------------------
# 2. record_decision — THE proven culprit (approval_tags / actor_ids / options).
#    Exercised through the validated tool-manager path.
# ---------------------------------------------------------------------------------------------

def test_record_decision_string_approval_tags_coerces_not_rejected(campaign):
    # THE REPRO: pre-fix this raised `Input should be a valid list [type=list_type ...]`
    # through the tool manager and tripped the FATAL no_rejected_tool_calls gate.
    res = _struct(_call("record_decision", {
        "campaign_id": campaign, "summary": "kept the bargain",
        "approval_tags": "honest_dealing",
    }))
    assert isinstance(res, dict) and "id" in res
    d = server._require(campaign).decisions[-1]
    assert d.approval_tags == ["honest_dealing"]


def test_record_decision_comma_actor_ids_coerces(campaign):
    res = _struct(_call("record_decision", {
        "campaign_id": campaign, "summary": "split the watch",
        "actor_ids": "id1,id2",
    }))
    assert isinstance(res, dict) and "id" in res
    d = server._require(campaign).decisions[-1]
    assert d.actor_ids == ["id1", "id2"]


def test_record_decision_comma_options_coerces(campaign):
    _struct(_call("record_decision", {
        "campaign_id": campaign, "summary": "which road",
        "options": "north,south,wait",
    }))
    d = server._require(campaign).decisions[-1]
    assert d.options == ["north", "south", "wait"]


def test_record_decision_real_list_unchanged(campaign):
    # ADDITIVE: a correct caller passing a real list is untouched (no double-wrap, no split).
    _struct(_call("record_decision", {
        "campaign_id": campaign, "summary": "real list",
        "approval_tags": ["mercy", "cruelty"], "actor_ids": ["pc_1", "pc_2"],
    }))
    d = server._require(campaign).decisions[-1]
    assert d.approval_tags == ["mercy", "cruelty"]
    assert d.actor_ids == ["pc_1", "pc_2"]


def test_record_decision_none_is_default(campaign):
    # None / omitted -> the additive no-op (empty lists), exactly as before the fix.
    _struct(_call("record_decision", {"campaign_id": campaign, "summary": "bare"}))
    d = server._require(campaign).decisions[-1]
    assert d.approval_tags == []
    assert d.actor_ids == []
    assert d.options == []


def test_record_decision_int_approval_tags_still_rejected(campaign):
    # A genuinely-wrong type must STILL be rejected loudly — we never swallow a real type bug.
    with pytest.raises(Exception) as ei:
        _call("record_decision", {
            "campaign_id": campaign, "summary": "bad type", "approval_tags": 5,
        })
    assert "list_type" in str(ei.value) or "valid list" in str(ei.value)


def test_record_decision_dict_actor_ids_still_rejected(campaign):
    with pytest.raises(Exception) as ei:
        _call("record_decision", {
            "campaign_id": campaign, "summary": "bad type", "actor_ids": {"a": 1},
        })
    assert "list_type" in str(ei.value) or "valid list" in str(ei.value)


# ---------------------------------------------------------------------------------------------
# 3. Nested model path — persist_beat's decision=dict builds a Decision whose list FIELDS
#    coerce too (the field-level BeforeValidator), so a string inside the nested dict is safe.
# ---------------------------------------------------------------------------------------------

def test_persist_beat_nested_decision_string_fields_coerce(campaign):
    res = _struct(_call("persist_beat", {
        "campaign_id": campaign,
        "decision": {
            "summary": "freed the bonded",
            "options": "free,sell",          # comma-string in the NESTED dict
            "actor_ids": "pc_1",             # bare string in the NESTED dict
            "approval_tags": "free_the_bonded",
        },
    }))
    assert isinstance(res, dict)
    d = server._require(campaign).decisions[-1]
    assert d.options == ["free", "sell"]
    assert d.actor_ids == ["pc_1"]
    assert d.approval_tags == ["free_the_bonded"]


# ---------------------------------------------------------------------------------------------
# 4. A combat id-list tool — start_combat.combatant_ids (required list[str]).
# ---------------------------------------------------------------------------------------------

def test_start_combat_comma_combatant_ids_coerces(campaign):
    a = server.create_character(campaign, "Hero", kind="player")["id"]
    b = server.create_character(campaign, "Goblin", kind="npc")["id"]
    res = _struct(_call("start_combat", {
        "campaign_id": campaign, "combatant_ids": f"{a},{b}",  # comma-string of ids
    }))
    assert isinstance(res, dict)
    c = server._require(campaign)
    assert c.combat.active
    ids = {cb.character_id for cb in c.combat.order}
    assert {a, b} <= ids


# ---------------------------------------------------------------------------------------------
# 5. author_companion_gauges — the char-split footgun: a bare string used to iterate
#    char-by-char into ['m','e','r','c','y']; the param coercion makes it ['mercy'].
# ---------------------------------------------------------------------------------------------

def test_author_companion_gauges_string_approval_likes_coerces(campaign):
    npc = server.create_character(campaign, "Toll", kind="npc")["id"]
    server.recruit_companion(campaign, npc_id=npc, class_name="Cleric", level=2,
                             abilities={"str": 10, "dex": 12, "con": 12, "int": 10,
                                        "wis": 14, "cha": 10})
    _struct(_call("author_companion_gauges", {
        "campaign_id": campaign, "companion_id": npc,
        "approval_likes": "mercy",          # bare string — pre-fix -> ['m','e','r','c','y']
        "approval_dislikes": "cruelty,greed",
    }))
    ch = server._char(server._require(campaign), npc)
    assert ch.companion_dossier.approval_likes == ["mercy"]
    assert ch.companion_dossier.approval_dislikes == ["cruelty", "greed"]
