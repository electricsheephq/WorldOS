#!/usr/bin/env python3
"""WorldOS #835 Live Composition — Increment 1: the stream tailer (Layer B).

Tails the DM's per-attempt stream-json `$out` file AS IT IS WRITTEN (the wrapper runs
`claude -p ... --output-format stream-json --include-partial-messages` only when
WORLDOS_STREAM_BEATS=1) and decodes the player-facing scene prose out of the streaming
TOOL ARGUMENT of the DM's `log_event(kind="narration"|"dialogue", text=...)` calls,
writing decoded chunks to `$STATE_DIR/stream/current.jsonl` for the viewer to poll.

Why the TOOL ARG and not assistant text (the keystone, from #835):
  A DM beat is multi-turn (tool calls + internal planning + the scene). Naively streaming
  assistant `text_delta` leaks scaffolding (the #732 class). But the DM writes its
  player-facing scene THROUGH `log_event(kind="narration", text=<scene>)`. With
  `--include-partial-messages` the CLI emits `content_block_start` carrying the tool NAME
  *before* its args stream, and the args then arrive as `input_json_delta.partial_json`
  chunks. So the prose is deterministically player-safe BY CONSTRUCTION — identified
  before its first word arrives — and we stream only the `text` value of narration/dialogue
  log_event calls.

This module is PURE-STDLIB and split into a testable parsing core (`StreamDecoder`) and a
thin file-tailing driver (`tail_stream` / `main`). The parser is the riskiest part and is
unit-tested in qa/test_stream_tailer.py. The tailer is a SIDECAR: if it crashes the DM beat
is unaffected (the wrapper kills it on beat end; the viewer simply stops seeing new chunks
and the canonical /events + /chat paths resolve the beat normally).

Increment 1 scope:
  * `log_event` (flat `text` arg) is the PRIMARY target — fully handled.
  * `persist_beat` (nested events list) is best-effort/skippable for Increment 1: its block
    is recognized as a candidate but its nested-`text` extraction is intentionally not wired
    (a later increment). It never streams scaffolding — worst case it streams nothing.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Callable, Iterable, Optional

# Tool-name suffixes whose tool-arg `text` carries player-facing prose. Matched on the bare
# name (after splitting off any MCP prefix, e.g. `mcp__clawdnd-engine__log_event` -> `log_event`),
# matching story_readout / latency_rollup's `.split("__")[-1]` convention. `persist_beat` is a
# candidate for recognition but its nested-events `text` extraction is deferred (Increment 1).
PROSE_TOOL_SUFFIXES = ("log_event", "persist_beat")
# Only these `kind` values are player-safe prose. A log_event with any other kind (system, roll,
# combat, ...) is NEVER streamed (its text is internal bookkeeping, not scene prose).
PROSE_KINDS = frozenset({"narration", "dialogue"})
# The engine's log_event accepts `text` (canonical) OR any of the aliases message/content/note as
# the prose value, normalizing them as `text = text or message or content or note` (server.py)
# — so a beat where the DM reaches for an alias still carries player-facing scene prose. The
# scanner therefore treats all of these as text-equivalent. PRECEDENCE mirrors the engine: a
# literal `text` value WINS over any alias when more than one appears in the same call (in
# practice the DM uses exactly one). Ordered most-preferred-first.
PROSE_TEXT_KEYS = ("text", "message", "content", "note")


def _short_tool(name: object) -> str:
    """Bare engine tool name from a possibly MCP-prefixed tool_use name.
    ``mcp__clawdnd-engine__log_event`` -> ``log_event`` (matches latency_rollup._short_tool)."""
    return str(name or "").split("__")[-1]


def _unwrap_event(row: dict) -> dict:
    """Return the raw Anthropic stream event from a stream-json row.

    The CLI's `--include-partial-messages` wraps each raw API SSE event. Depending on CLI
    version the event is either nested under an `event` key
    (`{"type":"stream_event","event":{"type":"content_block_start",...}}`) or the row IS the
    event itself (`{"type":"content_block_start",...}`). Tolerate BOTH so the tailer survives
    a CLI shape change."""
    ev = row.get("event")
    if isinstance(ev, dict):
        return ev
    return row


class _PartialJsonScanner:
    """Incrementally extract the `text` (and `kind`) string values from a JSON object whose
    serialization arrives in arbitrary chunks (the tool-arg `partial_json` deltas).

    We do NOT wait for a complete, parseable JSON object — the whole point is to stream the
    `text` value as it grows, mid-string. This is a small hand-rolled JSON-string state machine
    that:
      * tracks whether we are inside a string, and (when inside) whether that string is a KEY
        or a VALUE, and which key the current value belongs to;
      * decodes JSON string escapes (`\\"`, `\\n`, `\\uXXXX`, ...) INCREMENTALLY, carrying
        backslash / \\uXXXX state ACROSS chunk boundaries so a `\\"` or a `\\uXXXX` split
        between two deltas decodes correctly;
      * emits only fully-decoded characters of the `text` value (never a half-formed escape).

    Robustness: the scanner only ever READS the buffer forward; it never needs the object to be
    well-formed past the prose value, and a malformed tail simply stops producing new decoded
    text (the beat still resolves via the canonical paths). Only top-level keys are tracked
    (depth-aware), so a nested object's `text` (e.g. inside persist_beat's events list) does not
    masquerade as the flat narration text — and likewise a nested `message`/`content`/`note`.
    """

    def __init__(self) -> None:
        # Decoded values captured so far.
        self.kind: Optional[str] = None       # decoded `kind` value once its string closes
        self.text: str = ""                    # running decoded prefix of the prose value
        # Which prose key (PROSE_TEXT_KEYS) currently OWNS self.text. None until the first prose
        # value starts decoding. A later HIGHER-precedence key (e.g. a literal `text` after a
        # `note`) takes over and resets self.text; a later lower/equal-precedence key is ignored.
        self._text_key: Optional[str] = None
        self._text_closed = False              # the OWNING prose string has fully closed

        # Lexer state.
        self._in_string = False
        self._is_key = False                   # the current string is an object KEY
        self._cur_key: Optional[str] = None    # decoded chars of the in-progress key
        self._cur_val_key: Optional[str] = None  # which key the in-progress VALUE belongs to
        self._expect_value = False             # we just saw a `:` — the next string is a value
        self._depth = 0                        # object/array nesting; we track keys only at depth 1
        self._after_key = False                # we just closed a key string; awaiting `:`

        # Escape state, carried across chunk boundaries.
        self._escape = False                   # previous char was an unescaped backslash
        self._u_remaining = 0                  # \uXXXX hex digits still expected
        self._u_acc = ""                       # accumulated hex digits of the current \u escape

    @property
    def text_complete(self) -> bool:
        return self._text_closed

    def feed(self, chunk: str) -> None:
        """Consume a `partial_json` chunk, advancing the lexer + decoders."""
        for ch in chunk:
            self._consume(ch)

    # -- internals -----------------------------------------------------------------

    def _emit_decoded(self, decoded: str) -> None:
        """Route a fully-decoded character into the value it belongs to."""
        if self._is_key:
            self._cur_key = (self._cur_key or "") + decoded
            return
        # A VALUE character.
        if self._cur_val_key in PROSE_TEXT_KEYS and self._prose_key_owns(self._cur_val_key):
            self.text += decoded
        # `kind` is captured on string close (it's short); no need to stream it char-by-char.
        elif self._cur_val_key == "kind":
            # Buffer kind chars in self.text? No — keep them separate.
            self._kind_acc = getattr(self, "_kind_acc", "") + decoded

    def _prose_key_owns(self, key: str) -> bool:
        """Does prose-key `key` own self.text right now (so its decoded chars append)? The owner
        is the HIGHEST-precedence prose key seen so far (PROSE_TEXT_KEYS index 0 = highest). A
        same-or-higher precedence key (incl. the current owner) appends; a strictly higher one
        that just STARTED takes over (handled in _consume_structural on value-start, which resets
        self.text); a lower-precedence key never displaces the owner. Once the owning string has
        closed, no further appends (a stray later key can't extend a finished value)."""
        if self._text_closed:
            return False
        owner = self._text_key
        if owner is None:
            return True
        return PROSE_TEXT_KEYS.index(key) <= PROSE_TEXT_KEYS.index(owner)

    def _on_prose_value_start(self, key: Optional[str]) -> None:
        """A depth-1 value just STARTED for `key`. If it's a prose key (text or an alias) and
        strictly higher-precedence than the current owner, it takes over: reset the accumulated
        text and claim ownership — EVEN if the prior (lower-precedence) owner had already closed
        (e.g. a literal `text` appearing after a complete `note` discards the note's value,
        mirroring the engine's `text`-wins normalization). The common case — the FIRST prose key,
        or the SAME owner re-entered — just claims/keeps ownership. Non-prose keys (kind, speaker,
        …) are ignored here."""
        if key not in PROSE_TEXT_KEYS:
            return
        owner = self._text_key
        if owner is None:
            self._text_key = key
            return
        if PROSE_TEXT_KEYS.index(key) < PROSE_TEXT_KEYS.index(owner):
            # A higher-precedence prose key supersedes a previously-seen alias (closed or not).
            self._text_key = key
            self.text = ""
            self._text_closed = False

    def _consume(self, ch: str) -> None:
        if self._in_string:
            self._consume_in_string(ch)
            return
        self._consume_structural(ch)

    def _consume_in_string(self, ch: str) -> None:
        # Mid \uXXXX escape: accumulate exactly 4 hex digits, then emit one decoded char.
        if self._u_remaining > 0:
            self._u_acc += ch
            self._u_remaining -= 1
            if self._u_remaining == 0:
                try:
                    self._emit_decoded(chr(int(self._u_acc, 16)))
                except ValueError:
                    pass  # malformed \u — drop it rather than throw (sidecar must never crash)
                self._u_acc = ""
            return
        if self._escape:
            self._escape = False
            if ch == "u":
                self._u_remaining = 4
                self._u_acc = ""
                return
            self._emit_decoded(_JSON_ESCAPES.get(ch, ch))
            return
        if ch == "\\":
            self._escape = True
            return
        if ch == '"':
            # String closes.
            self._in_string = False
            if self._is_key:
                self._after_key = True
            else:
                # A value string just closed — finalize captured values.
                if self._cur_val_key == "kind":
                    self.kind = getattr(self, "_kind_acc", "")
                    self._kind_acc = ""
                elif self._cur_val_key in PROSE_TEXT_KEYS and self._cur_val_key == self._text_key:
                    # The OWNING prose value (text or the winning alias) closed → no more appends.
                    self._text_closed = True
                self._cur_val_key = None
            self._is_key = False
            return
        # An ordinary in-string character.
        self._emit_decoded(ch)

    def _consume_structural(self, ch: str) -> None:
        if ch == '"':
            self._in_string = True
            # At object depth 1, a string is a KEY when we're NOT expecting a value; otherwise a VALUE.
            if self._depth == 1 and not self._expect_value:
                self._is_key = True
                self._cur_key = ""
            else:
                self._is_key = False
                if self._expect_value and self._depth == 1:
                    self._cur_val_key = self._pending_key
                    self._on_prose_value_start(self._cur_val_key)
                self._expect_value = False
            return
        if ch == ":":
            if self._after_key:
                self._pending_key = self._cur_key
                self._after_key = False
                self._expect_value = True
            return
        if ch in "{[":
            self._depth += 1
            self._expect_value = False
            self._after_key = False
            return
        if ch in "}]":
            self._depth -= 1
            self._expect_value = False
            self._after_key = False
            return
        if ch == ",":
            self._expect_value = False
            self._after_key = False
            return
        # whitespace / digits / other scalars between structure — ignored for our purpose.


# JSON single-char escape map (excluding \u which is handled separately).
_JSON_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class StreamDecoder:
    """Stateful decoder over a sequence of stream-json ROWS (dicts). Drives one
    `_PartialJsonScanner` per CANDIDATE tool_use block (a log_event/persist_beat call), and
    emits decoded prose chunks for narration/dialogue ones.

    The emit callback is invoked with the NEWLY-DECODED text delta (the increment since the
    last emit for that block), already gated on a KNOWN player-safe `kind`. Key-ORDER is
    handled: if `kind` is not yet known when `text` starts decoding, the decoded text is
    BUFFERED and only flushed once `kind` resolves to a prose kind (and discarded if it
    resolves to a non-prose kind). This handles `kind` appearing BEFORE or AFTER `text`.

    Pure: feed rows, get callbacks. The file-tailing driver below adapts a growing file to this.
    """

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        # Per-block-index state. A block is a CANDIDATE if its tool name suffix is a prose tool.
        # state: {"scanner", "tool", "emitted": int(chars already emitted), "buffered": bool}
        self._blocks: dict[int, dict] = {}

    def feed_row(self, row: dict) -> None:
        if not isinstance(row, dict):
            return
        ev = _unwrap_event(row)
        if not isinstance(ev, dict):
            return
        etype = ev.get("type")
        if etype == "content_block_start":
            self._on_block_start(ev)
        elif etype == "content_block_delta":
            self._on_block_delta(ev)
        elif etype == "content_block_stop":
            self._on_block_stop(ev)
        # message_start/message_delta/message_stop/result and assistant text_delta are ignored:
        # we NEVER stream assistant text in Increment 1 (scaffolding risk, #732 class).

    def _on_block_start(self, ev: dict) -> None:
        idx = ev.get("index")
        block = ev.get("content_block") or {}
        if not isinstance(block, dict):
            return
        if block.get("type") != "tool_use":
            return
        tool = _short_tool(block.get("name"))
        if tool not in PROSE_TOOL_SUFFIXES:
            return
        # Mark this block index as a CANDIDATE. `persist_beat` is recognized but its nested-`text`
        # is not extracted in Increment 1 (the scanner only flushes a depth-1 flat `text`).
        if isinstance(idx, int):
            self._blocks[idx] = {
                "scanner": _PartialJsonScanner(),
                "tool": tool,
                "emitted": 0,
            }

    def _on_block_delta(self, ev: dict) -> None:
        idx = ev.get("index")
        st = self._blocks.get(idx) if isinstance(idx, int) else None
        if st is None:
            return
        delta = ev.get("delta") or {}
        if not isinstance(delta, dict) or delta.get("type") != "input_json_delta":
            return
        partial = delta.get("partial_json")
        if not isinstance(partial, str) or not partial:
            return
        st["scanner"].feed(partial)
        self._maybe_emit(st)

    def _on_block_stop(self, ev: dict) -> None:
        idx = ev.get("index")
        st = self._blocks.pop(idx, None) if isinstance(idx, int) else None
        if st is None:
            return
        # Final flush: kind may have resolved exactly at close; surface any remaining buffered text.
        self._maybe_emit(st)

    def _maybe_emit(self, st: dict) -> None:
        """Emit the newly-decoded prose for a candidate block, gated on a known prose `kind`.

        Buffer-until-kind: while `kind` is unknown the decoded `text` accumulates in the scanner
        but is NOT emitted. Once `kind` is known:
          * prose kind  -> flush everything decoded-so-far (catches the BUFFERED prefix) and keep
            streaming the tail;
          * non-prose   -> never emit (mark the block so we stop checking).
        For `persist_beat` we never emit in Increment 1 (its flat depth-1 `text` is absent — the
        prose lives in a nested events list — so the scanner's `text` stays empty; this is a
        no-op, never a leak).
        """
        if st.get("suppressed"):
            return
        scanner = st["scanner"]
        kind = scanner.kind
        if kind is None:
            # Kind not known yet — buffer (do nothing). The decoded text is retained in the
            # scanner and will be flushed retroactively once kind resolves.
            return
        if kind not in PROSE_KINDS:
            # A non-narration/dialogue log_event (system/roll/combat) — NEVER stream it.
            st["suppressed"] = True
            return
        if st["tool"] != "log_event":
            # persist_beat (nested events) — deferred for Increment 1; nothing to flush.
            return
        full = scanner.text
        already = st["emitted"]
        if already > len(full):
            # The scanner's prose ACCUMULATOR shrank — a higher-precedence prose key (text after
            # an alias) reset it (engine `text`-wins normalization). We can't retract chunks
            # already flushed downstream, but realign so the new owner's prose still streams from
            # here (a non-issue in practice: the DM uses one prose key per call).
            already = 0
            st["emitted"] = 0
        if len(full) > already:
            delta = full[already:]
            st["emitted"] = len(full)
            if delta:
                self._emit(delta)


# ------------------------------------------------------------------------------------------
# File-tailing driver (the side-effectful half; the parsing core above is what tests exercise).
# ------------------------------------------------------------------------------------------


def _iter_new_lines(path: str, state: dict) -> Iterable[str]:
    """Yield any COMPLETE new lines appended to `path` since the last call.

    Carries a byte offset + a partial-line tail across calls so a line split across two
    appends (writes can exceed PIPE_BUF) is only yielded once fully terminated by a newline.
    Tolerates the file not existing yet (the attempt may not have created `$out` at launch)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    offset = state.get("offset", 0)
    if size < offset:
        # The file shrank/rotated (a retry minted a fresh $out at the same path is not our case —
        # the wrapper hands us the newest path — but be defensive): restart from the top.
        offset = 0
        state["tail"] = ""
    if size == offset:
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            data = f.read()
            state["offset"] = f.tell()
    except OSError:
        return
    buf = state.get("tail", "") + data
    lines = buf.split("\n")
    state["tail"] = lines.pop()  # trailing partial (or "" if data ended on a newline)
    for line in lines:
        yield line


def _open_sink(stream_path: str):
    """Open (and TRUNCATE) the stream sink at beat start, returning the file handle. Truncating
    here implements 'reset on each new beat/attempt': the wrapper launches a fresh tailer per
    attempt, so opening with "w" clears any prior attempt's chunks."""
    os.makedirs(os.path.dirname(stream_path), exist_ok=True)
    return open(stream_path, "w", encoding="utf-8")


# SELF-BOUNDING DEFAULTS (#835 Increment 2, FIX B). The wrapper kills the tailer on a clean beat
# end (worldos_stream_tailer_stop) AND on a signal-trap exit (the _cleanup/_party_cleanup traps),
# but a stop signal can still be MISSED (a hard kill of the parent, a crashed wrapper, an orphan
# left by a deadline-killed runner). So the tailer ALSO self-terminates: an absolute wall-clock
# lifetime cap, and an idle cap (the target $out stopped growing for this long → the beat is over).
# Either bound makes the sidecar incapable of running forever even if its stop signal never comes.
DEFAULT_MAX_LIFETIME_S = 1800.0   # hard ceiling from tailer start (a DM beat is bounded well under this)
DEFAULT_MAX_IDLE_S = 180.0        # no new $out growth for this long → the beat has ended; exit


def tail_stream(
    out_path: str,
    stream_path: str,
    *,
    poll_interval: float = 0.25,
    stop: Optional[Callable[[], bool]] = None,
    max_idle_s: Optional[float] = DEFAULT_MAX_IDLE_S,
    max_lifetime_s: Optional[float] = DEFAULT_MAX_LIFETIME_S,
) -> int:
    """Tail `out_path` (the DM stream-json file) and write decoded prose chunks to
    `stream_path` ($STATE_DIR/stream/current.jsonl), one JSON line per chunk. Returns the
    number of chunks written. Runs until `stop()` returns True (when provided) or the process
    is killed by the wrapper at beat end — OR until a SELF-BOUNDING cap trips (FIX B), so a
    missed stop signal can never leave the sidecar running forever:
      * `max_idle_s` — stop after this long with no new $out growth (the beat is over). Default
        DEFAULT_MAX_IDLE_S; pass None to disable.
      * `max_lifetime_s` — a hard wall-clock ceiling from start. Default DEFAULT_MAX_LIFETIME_S;
        pass None to disable.
    Both are belt-and-suspenders to the wrapper's explicit kill, not a replacement for it."""
    state: dict = {"offset": 0, "tail": ""}
    seq = 0
    sink = _open_sink(stream_path)

    def emit(delta: str) -> None:
        nonlocal seq
        row = {"seq": seq, "text": delta, "ts": time.time()}
        sink.write(json.dumps(row, ensure_ascii=False) + "\n")
        sink.flush()
        seq += 1

    decoder = StreamDecoder(emit)
    started = time.time()
    last_progress = started
    try:
        while True:
            if stop is not None and stop():
                break
            # Hard lifetime cap: trips regardless of activity (a stuck-but-growing $out can't pin
            # the sidecar open past this). Checked first so it always wins.
            if max_lifetime_s is not None and (time.time() - started) > max_lifetime_s:
                break
            progressed = False
            for line in _iter_new_lines(out_path, state):
                progressed = True
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # half-written or non-JSON wrapper line — skip
                decoder.feed_row(row)
            if progressed:
                last_progress = time.time()
            elif max_idle_s is not None and (time.time() - last_progress) > max_idle_s:
                break
            time.sleep(poll_interval)
    finally:
        try:
            sink.close()
        except OSError:
            pass
    return seq


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        sys.stderr.write("usage: stream_tailer.py <dm_out.jsonl> <stream_dir_or_current.jsonl>\n")
        return 2
    out_path = argv[0]
    target = argv[1]
    # Accept either the stream DIR ($STATE_DIR/stream) or the file path directly.
    if os.path.isdir(target) or not target.endswith(".jsonl"):
        stream_path = os.path.join(target, "current.jsonl")
    else:
        stream_path = target
    poll = float(os.environ.get("WORLDOS_STREAM_POLL_S", "0.25"))
    # FIX B self-bounding: env-overridable hard lifetime + idle caps so a tailer whose stop signal
    # is missed (orphaned by a hard parent kill / deadline) still self-terminates. 0/negative or a
    # non-numeric value DISABLES the respective cap (None). Defaults match tail_stream's.
    max_lifetime = _env_bound("WORLDOS_STREAM_TAILER_MAX_S", DEFAULT_MAX_LIFETIME_S)
    max_idle = _env_bound("WORLDOS_STREAM_TAILER_IDLE_S", DEFAULT_MAX_IDLE_S)
    try:
        tail_stream(
            out_path, stream_path,
            poll_interval=poll,
            max_idle_s=max_idle,
            max_lifetime_s=max_lifetime,
        )
    except KeyboardInterrupt:
        pass
    return 0


def _env_bound(name: str, default: float) -> Optional[float]:
    """Resolve a seconds bound from env: a positive float overrides `default`; a 0/negative or
    unparseable value DISABLES the bound (returns None); unset → `default`."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        return None
    return val if val > 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
