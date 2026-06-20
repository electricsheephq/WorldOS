#!/usr/bin/env python3
"""Tests for scripts/stream_tailer.py — WorldOS #835 Live Composition Increment 1.

The tailer's partial-JSON parsing is the riskiest part of the feature, so these tests feed
SYNTHETIC stream-json sequences (content_block_start log_event + input_json_delta chunks)
through the pure StreamDecoder and assert:
  * the decoded prose equals the intended scene text;
  * `kind` BEFORE *and* AFTER `text` both decode correctly (buffer-until-kind);
  * partial_json split across arbitrary chunk boundaries (incl. mid `\\"` and mid `\\uXXXX`)
    decodes correctly;
  * a non-narration kind (system/roll) is NEVER streamed;
  * multiple sequential log_event calls in one beat stream in order;
  * MCP-prefixed tool names (mcp__clawdnd-engine__log_event) are recognized;
  * both the nested-`event` and flat stream-json row shapes are tolerated;
  * the file-tailing driver decodes a real growing-file stream.

Pure-stdlib; SINGLE-PROCESS (no xdist). Run with:
    python3 -m pytest qa/test_stream_tailer.py -q -p no:xdist
or:
    uv run --directory servers/engine python -m pytest qa/test_stream_tailer.py -q -p no:xdist
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import stream_tailer  # noqa: E402


# ---------------------------------------------------------------------------------------------
# Helpers: build synthetic stream-json rows mirroring `claude -p --include-partial-messages`.
# ---------------------------------------------------------------------------------------------

def _row(event: dict, *, nested: bool = True) -> dict:
    """Wrap a raw API stream `event` as the CLI emits it. `nested=True` -> the canonical
    `{"type":"stream_event","event":{...}}`; `nested=False` -> the flattened shape (the event
    fields at top level) — the tailer must tolerate both."""
    if nested:
        return {"type": "stream_event", "event": event, "session_id": "s", "uuid": "u"}
    return dict(event)


def block_start(index: int, name: str, *, nested: bool = True) -> dict:
    return _row({
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "tool_use", "id": f"toolu_{index}", "name": name, "input": {}},
    }, nested=nested)


def block_delta(index: int, partial: str, *, nested: bool = True) -> dict:
    return _row({
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "input_json_delta", "partial_json": partial},
    }, nested=nested)


def block_stop(index: int, *, nested: bool = True) -> dict:
    return _row({"type": "content_block_stop", "index": index}, nested=nested)


def _decode(rows, **kw):
    """Run rows through StreamDecoder, returning the concatenated emitted prose."""
    out = []
    dec = stream_tailer.StreamDecoder(out.append)
    for r in rows:
        dec.feed_row(r)
    return "".join(out)


def _chunks_of(s: str, size: int):
    return [s[i:i + size] for i in range(0, len(s), size)]


# ---------------------------------------------------------------------------------------------
# Core decoding: kind ordering.
# ---------------------------------------------------------------------------------------------

def test_kind_before_text():
    """The canonical order: {"kind":"narration","text":"<scene>"} — streams the full scene."""
    scene = "The weathered lantern flickers above the tavern door as dusk bleeds purple."
    arg = json.dumps({"kind": "narration", "text": scene})
    rows = [block_start(1, "log_event")] + [block_delta(1, c) for c in _chunks_of(arg, 7)] + [block_stop(1)]
    assert _decode(rows) == scene


def test_kind_after_text_buffers_until_known():
    """When `text` appears BEFORE `kind`, the decoded prose must be buffered and flushed
    retroactively once `kind` resolves to narration — not dropped, not leaked early."""
    scene = "Lantern light smears the rain-slicked streets into silver rivers."
    arg = json.dumps({"text": scene, "kind": "dialogue"})  # text first, then kind
    rows = [block_start(2, "log_event")] + [block_delta(2, c) for c in _chunks_of(arg, 9)] + [block_stop(2)]
    # dialogue is a prose kind → the full text streams.
    assert _decode(rows) == scene


def test_kind_after_text_nonprose_never_streams():
    """text-before-kind where kind resolves to a NON-prose kind → nothing is ever emitted
    (the buffered text is discarded once kind is known to be non-prose)."""
    arg = json.dumps({"text": "rolled 17 vs AC 14 — hit", "kind": "roll"})
    rows = [block_start(3, "log_event")] + [block_delta(3, c) for c in _chunks_of(arg, 5)] + [block_stop(3)]
    assert _decode(rows) == ""


# ---------------------------------------------------------------------------------------------
# Core decoding: non-prose kinds are never streamed (kind-first too).
# ---------------------------------------------------------------------------------------------

def test_nonprose_kind_first_never_streams():
    arg = json.dumps({"kind": "system", "text": "State grounded; closing turn."})
    rows = [block_start(1, "log_event")] + [block_delta(1, c) for c in _chunks_of(arg, 8)] + [block_stop(1)]
    assert _decode(rows) == ""


def test_combat_kind_never_streams():
    arg = json.dumps({"kind": "combat", "text": "Goblin takes 6 slashing."})
    rows = [block_start(1, "log_event"), block_delta(1, arg), block_stop(1)]
    assert _decode(rows) == ""


# ---------------------------------------------------------------------------------------------
# Chunk-boundary robustness: split the SAME scene at every possible boundary + adversarial sizes.
# ---------------------------------------------------------------------------------------------

def test_chunk_boundaries_every_split_size():
    scene = 'A "weathered" sign creaks; the harbor wind carries salt and woodsmoke.'
    arg = json.dumps({"kind": "narration", "text": scene})
    for size in range(1, len(arg) + 1):
        rows = [block_start(1, "log_event")] + [block_delta(1, c) for c in _chunks_of(arg, size)] + [block_stop(1)]
        assert _decode(rows) == scene, f"failed at chunk size {size}"


def test_escaped_quote_split_across_chunks():
    """A JSON `\\"` escape split BETWEEN two deltas (backslash in chunk N, quote in chunk N+1)
    must decode to a single `"` — not terminate the string early."""
    scene = 'She said, "hold the line," and drew her blade.'
    arg = json.dumps({"kind": "narration", "text": scene})
    # Find a `\"` and split exactly between the backslash and the quote.
    idx = arg.find('\\"')
    assert idx != -1
    chunks = [arg[:idx + 1], arg[idx + 1:]]  # backslash ends chunk 0; quote starts chunk 1
    rows = [block_start(1, "log_event")] + [block_delta(1, c) for c in chunks] + [block_stop(1)]
    assert _decode(rows) == scene


def test_unicode_escape_split_mid_sequence():
    """A `\\uXXXX` escape split mid-sequence (across one or more chunk boundaries) must decode
    to the correct single character."""
    scene = "dusk bleeds purple — a long dash é accent."  # em-dash + e-acute
    arg = json.dumps({"kind": "narration", "text": scene}, ensure_ascii=True)  # forces \uXXXX
    assert "\\u" in arg
    # Split at size 3 so a \uXXXX (6 chars: \,u,X,X,X,X) is guaranteed cut mid-sequence somewhere.
    rows = [block_start(1, "log_event")] + [block_delta(1, c) for c in _chunks_of(arg, 3)] + [block_stop(1)]
    assert _decode(rows) == scene


def test_newline_and_tab_escapes():
    scene = "Line one.\nLine two.\tTabbed."
    arg = json.dumps({"kind": "narration", "text": scene})
    rows = [block_start(1, "log_event")] + [block_delta(1, c) for c in _chunks_of(arg, 4)] + [block_stop(1)]
    assert _decode(rows) == scene


# ---------------------------------------------------------------------------------------------
# Multiple sequential log_event calls in one beat → ordered concatenation.
# ---------------------------------------------------------------------------------------------

def test_multiple_log_events_stream_in_order():
    a = "First, the door groans open."
    b = "Then, a figure steps from the dark."
    arg_a = json.dumps({"kind": "narration", "text": a})
    arg_b = json.dumps({"kind": "dialogue", "text": b})
    rows = (
        [block_start(1, "log_event")] + [block_delta(1, c) for c in _chunks_of(arg_a, 6)] + [block_stop(1)]
        + [block_start(2, "log_event")] + [block_delta(2, c) for c in _chunks_of(arg_b, 6)] + [block_stop(2)]
    )
    assert _decode(rows) == a + b


def test_interleaved_nonprose_between_prose_calls():
    """A roll log_event between two narration calls must not pollute the streamed prose."""
    a = "The blade sings free."
    b = "Blood beads on the cobbles."
    roll = json.dumps({"kind": "roll", "text": "d20=18"})
    arg_a = json.dumps({"kind": "narration", "text": a})
    arg_b = json.dumps({"kind": "narration", "text": b})
    rows = (
        [block_start(1, "log_event")] + [block_delta(1, arg_a)] + [block_stop(1)]
        + [block_start(2, "log_event")] + [block_delta(2, roll)] + [block_stop(2)]
        + [block_start(3, "log_event")] + [block_delta(3, arg_b)] + [block_stop(3)]
    )
    assert _decode(rows) == a + b


# ---------------------------------------------------------------------------------------------
# Tool-name handling: MCP prefixes + non-target tools.
# ---------------------------------------------------------------------------------------------

def test_mcp_prefixed_log_event_name_recognized():
    scene = "The market square wakes under a bruised sky."
    arg = json.dumps({"kind": "narration", "text": scene})
    rows = [block_start(1, "mcp__clawdnd-engine__log_event"), block_delta(1, arg), block_stop(1)]
    assert _decode(rows) == scene


def test_non_target_tool_ignored():
    """A non-prose tool (e.g. roll_dice / attack) is NOT a candidate — its args never stream."""
    arg = json.dumps({"expression": "1d20+5", "text": "should not stream"})
    rows = [block_start(1, "mcp__clawdnd-engine__roll_dice"), block_delta(1, arg), block_stop(1)]
    assert _decode(rows) == ""


def test_assistant_text_delta_never_streams():
    """A `text_delta` (assistant prose, NOT a tool arg) must never stream in Increment 1."""
    rows = [_row({
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "internal planning scaffolding..."},
    })]
    assert _decode(rows) == ""


def test_persist_beat_recognized_but_not_streamed_increment1():
    """persist_beat is a recognized candidate but its nested-events `text` is deferred — it must
    NOT leak its top-level args, and (Increment 1) streams nothing."""
    arg = json.dumps({"events": [{"kind": "narration", "text": "nested scene"}]})
    rows = [block_start(1, "persist_beat")] + [block_delta(1, c) for c in _chunks_of(arg, 5)] + [block_stop(1)]
    assert _decode(rows) == ""


# ---------------------------------------------------------------------------------------------
# Row-shape tolerance: flat (non-nested) stream-json rows.
# ---------------------------------------------------------------------------------------------

def test_flat_row_shape_tolerated():
    scene = "A flat-shaped event stream still decodes."
    arg = json.dumps({"kind": "narration", "text": scene})
    rows = (
        [block_start(1, "log_event", nested=False)]
        + [block_delta(1, c, nested=False) for c in _chunks_of(arg, 5)]
        + [block_stop(1, nested=False)]
    )
    assert _decode(rows) == scene


# ---------------------------------------------------------------------------------------------
# File-tailing driver: a real growing file → decoded chunks in current.jsonl.
# ---------------------------------------------------------------------------------------------

def test_tail_stream_driver_decodes_growing_file(tmp_path):
    scene = "The campfire gutters; shadows lengthen across the ruined keep."
    arg = json.dumps({"kind": "narration", "text": scene})
    lines = (
        [block_start(1, "log_event")]
        + [block_delta(1, c) for c in _chunks_of(arg, 11)]
        + [block_stop(1)]
    )
    out_path = tmp_path / "dm.jsonl"
    # Write the whole stream up front (the driver reads from offset 0 forward), include a
    # trailing partial line to prove the tail-carry never yields it.
    body = "\n".join(json.dumps(r) for r in lines) + "\n" + '{"type":"stream_eve'  # half line
    out_path.write_text(body, encoding="utf-8")

    stream_path = tmp_path / "stream" / "current.jsonl"
    # stop() after one pass: the file is already fully written, so one read decodes everything.
    passes = {"n": 0}

    def stop():
        passes["n"] += 1
        return passes["n"] > 2  # let it read + idle a couple of cycles

    written = stream_tailer.tail_stream(
        str(out_path), str(stream_path), poll_interval=0.001, stop=stop
    )
    assert written >= 1
    decoded = "".join(
        json.loads(ln)["text"]
        for ln in stream_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    )
    assert decoded == scene


def test_tail_stream_truncates_sink_on_start(tmp_path):
    """The sink (current.jsonl) is truncated at beat start (reset on each new attempt)."""
    stream_path = tmp_path / "stream" / "current.jsonl"
    stream_path.parent.mkdir(parents=True)
    stream_path.write_text('{"seq":99,"text":"STALE FROM PRIOR BEAT","ts":0}\n', encoding="utf-8")
    out_path = tmp_path / "dm.jsonl"
    out_path.write_text("", encoding="utf-8")
    stream_tailer.tail_stream(str(out_path), str(stream_path), poll_interval=0.001, stop=lambda: True)
    # Opened with "w" → the stale row is gone.
    assert "STALE FROM PRIOR BEAT" not in stream_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-q", "-p", "no:xdist"]))
